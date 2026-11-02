"""Shared ESP32/server gallery format: MBF512V1, little endian float32."""
import hashlib
import json
import math
import struct
from face_database import FaceDatabase, normalized

HEADER = struct.Struct('<8s64sffI')
RECORD = struct.Struct('<64s320s512f')
MAX_SAMPLES = 250


def gallery(database, threshold=0.5, margin=0.05):
    if not math.isfinite(threshold) or not -1 <= threshold <= 1 or not math.isfinite(margin) or not 0 <= margin <= 2:
        raise ValueError('Invalid threshold/margin')
    with database.lock:
        data = json.loads(json.dumps(database.data))
    if len(data['people']) > 50:
        raise ValueError('Maximum 50 identities')
    records = []
    for person in data['people']:
        identity, name = person['id'].encode(), person['name'].encode('utf-8')
        if len(identity) > 63 or len(name) > 319 or not identity or b'\0' in identity or b'\0' in name:
            raise ValueError('ID/name too long or invalid for edge gallery')
        person['samples'] = person['samples'][-5:]
        for sample in person['samples']:
            vector = sample['embedding']
            if len(vector) != 512:
                raise ValueError('Edge requires 512-dimensional MBF_S8_V1 embeddings')
            records.append(RECORD.pack(identity, name, *vector))
    raw = b''.join(records)
    revision = hashlib.sha256(struct.pack('<ff', threshold, margin)+raw).hexdigest()
    binary = HEADER.pack(b'MBF512V1', revision.encode(), threshold, margin, len(records)) + raw
    # Recognize against exactly the float32 vectors distributed to devices.
    trimmed = FaceDatabase.__new__(FaceDatabase)
    import threading
    trimmed.lock = threading.RLock()
    trimmed.data = data
    for person in trimmed.data['people']:
        for sample in person['samples']:
            sample['embedding'] = normalized(list(struct.unpack('<512f', struct.pack('<512f', *sample['embedding']))))
    return binary, trimmed, revision
