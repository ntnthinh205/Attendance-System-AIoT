"""Local JSON enrollment and cosine matching for ESP32 MBF embeddings."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import uuid
from datetime import datetime, timezone

MODEL = 'MBF_S8_V1'
QUALITY_MESSAGES = {
    'too_far': 'Đưa mặt lại gần camera hơn.',
    'cropped': 'Lùi nhẹ, giữ toàn bộ khuôn mặt trong ảnh.',
    'tilted': 'Giữ đầu thẳng, không nghiêng sang vai.',
    'turned': 'Nhìn thẳng vào camera, không quay mặt sang bên.',
    'pitch': 'Đặt camera ngang tầm mắt, không cúi hoặc ngửa mặt.',
    'low_confidence': 'Camera chưa thấy rõ mặt. Nhìn thẳng và tăng ánh sáng.',
    'invalid_landmarks': 'Chưa thấy rõ mắt, mũi và miệng. Nhìn thẳng camera.',
    'embedding_failed': 'Chưa tạo được embedding, hãy giữ mặt yên và thử lại.',
    'no_face': 'Đưa một khuôn mặt vào camera.',
    'multiple_faces': 'Chỉ để một khuôn mặt trong khung hình.',
}



def normalized(values):
    if not isinstance(values, list) or not values or len(values) > 4096:
        raise ValueError('Embedding không hợp lệ.')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('Embedding phải chứa các số hữu hạn.')
    norm = math.sqrt(sum(v * v for v in values))
    if not 0.9 <= norm <= 1.1:
        raise ValueError('Embedding chưa được chuẩn hóa L2.')
    return [v / norm for v in values]


def face_vector(face):
    if not isinstance(face, dict) or face.get('model') != MODEL or face.get('faces') != 1:
        raise ValueError('Cần đúng một khuôn mặt trong khung hình.')
    quality = face.get('quality')
    if isinstance(quality, dict) and quality.get('accepted') is not True:
        raise ValueError(QUALITY_MESSAGES.get(quality.get('status'), 'Khuôn mặt chưa đạt điều kiện chất lượng.'))
    vector = normalized(face.get('embedding'))
    if face.get('dimensions') != len(vector):
        raise ValueError('Số chiều embedding không khớp.')
    return vector


class FaceDatabase:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.data = dict(version=1, model=MODEL, dimensions=None, people=[])
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != 1 or data.get('model') != MODEL or not isinstance(data.get('people'), list):
                raise ValueError('Database sai định dạng hoặc khác model.')
            ids, names = set(), set()
            for person in data['people']:
                if not isinstance(person.get('id'), str) or not isinstance(person.get('name'), str) or not person['name'].strip():
                    raise ValueError('Danh tính trong database không hợp lệ.')
                if person['id'] in ids or person['name'].casefold() in names:
                    raise ValueError('Database có danh tính trùng.')
                ids.add(person['id'])
                names.add(person['name'].casefold())
                if not isinstance(person.get('samples'), list) or not person['samples']:
                    raise ValueError('Danh tính thiếu mẫu embedding.')
                for sample in person['samples']:
                    vector = normalized(sample['embedding'])
                    if len(vector) != data.get('dimensions'):
                        raise ValueError('Database chứa vector khác số chiều.')
                    sample['embedding'] = vector
            self.data = data

    def people(self):
        with self.lock:
            return [dict(id=p['id'], name=p['name'], samples=len(p['samples'])) for p in self.data['people']]

    def enroll(self, name, face):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError('Tên phải có từ 1 đến 80 ký tự, không chứa ký tự điều khiển.')
        name = name.strip()
        vector = face_vector(face)
        with self.lock:
            data = copy.deepcopy(self.data)
            if data['dimensions'] not in (None, len(vector)):
                raise ValueError('Embedding khác số chiều với database.')
            data['dimensions'] = len(vector)
            person = next((p for p in data['people'] if p['name'].casefold() == name.casefold()), None)
            if person is None:
                person = dict(id=uuid.uuid4().hex, name=name, samples=[])
                data['people'].append(person)
            if len(person['samples']) >= 20:
                raise ValueError('Tên này đã có 20 mẫu. Hãy dùng các mẫu đã đăng ký.')
            person['samples'].append(dict(embedding=vector, created_at=datetime.now(timezone.utc).isoformat()))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as stream:
                    tmp = stream.name
                    json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.path)
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
            self.data = data
            return dict(id=person['id'], name=person['name'], samples=len(person['samples']))

    def recognize(self, face, threshold=0.5, margin=0.05):
        try:
            vector = face_vector(face)
        except ValueError as exc:
            return dict(status='unavailable', reason=str(exc))
        with self.lock:
            if not self.data['people']:
                return dict(status='empty', reason='Chưa có khuôn mặt đăng ký.')
            if len(vector) != self.data['dimensions']:
                return dict(status='unavailable', reason='Số chiều khác database.')
            scores = sorted([(max(sum(a*b for a, b in zip(vector, s['embedding'])) for s in p['samples']), p)
                             for p in self.data['people']], key=lambda item: item[0], reverse=True)
            score, person = scores[0]
            gap = score - scores[1][0] if len(scores) > 1 else None
            matched = score >= threshold and (gap is None or gap >= margin)
            return dict(status='matched' if matched else 'unknown', name=person['name'] if matched else None,
                        id=person['id'] if matched else None, similarity=round(max(-1., min(1., score)), 4),
                        threshold=threshold, margin=margin,
                        reason='' if matched else 'Chưa đăng ký / chưa đủ độ tương đồng hoặc hai danh tính quá gần nhau.')
