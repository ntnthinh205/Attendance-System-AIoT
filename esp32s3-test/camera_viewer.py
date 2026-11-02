"""USB camera viewer: python camera_viewer.py --port /dev/ttyACM0."""
import argparse
import base64
import binascii
import json
import ipaddress
import secrets
import math
from pathlib import Path
from face_database import FaceDatabase, QUALITY_MESSAGES
from collections import deque
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial

PAGE = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ESP32 Camera</title>
<style>body{background:#151922;color:#eee;font:18px sans-serif;text-align:center}.preview{position:relative;width:min(90vw,960px);margin:auto;aspect-ratio:4/3;background:#080a10}#camera{display:block;width:100%;height:100%}#guide,#boxes{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}p{color:#aebdd0}input,button{font:inherit;padding:10px;border-radius:8px;border:1px solid #566173;margin:4px}button{cursor:pointer;background:#4dff88;color:#102017}button:disabled{opacity:.5;cursor:wait}.enroll{max-width:960px;margin:18px auto}#recognition{font-size:24px;color:#4dff88}#people{white-space:pre-line}small{color:#aebdd0}</style>
<h1>ESP32-S3 / OV3660</h1><p id="status">Waiting for camera over USB...</p><p id="ai">Waiting for MBF_S8_V1...</p><div class="preview"><img id="camera" alt="Camera preview"><svg id="guide" viewBox="0 0 320 240" role="img" aria-label="Khung hướng dẫn: đặt mặt ở giữa ảnh, hai mắt ngang đường nét đứt">
<defs><mask id="guide-mask"><rect width="320" height="240" fill="white"/><rect x="90" y="26" width="140" height="188" rx="30" fill="black"/></mask></defs>
<rect width="320" height="240" fill="#050a13" opacity=".45" mask="url(#guide-mask)"/>
<rect x="90" y="26" width="140" height="188" rx="30" fill="none" stroke="#89ccff" stroke-width="1" stroke-dasharray="4 4" opacity=".8"/>
<path d="M90 61V50Q90 26 114 26H128 M192 26H206Q230 26 230 50V61 M230 179V190Q230 214 206 214H192 M128 214H114Q90 214 90 190V179" fill="none" stroke="#89ccff" stroke-width="3" stroke-linecap="round"/>
<ellipse cx="160" cy="117" rx="60" ry="78" fill="none" stroke="#b9dfff" stroke-width=".8" stroke-dasharray="3 4" opacity=".5"/>
<path d="M114 94H206" fill="none" stroke="#b9dfff" stroke-width="1" stroke-dasharray="3 3" opacity=".65"/>
<g fill="white" font-family="sans-serif" text-anchor="middle" stroke="#0c1420" stroke-width="3" paint-order="stroke">
<text x="160" y="17" font-size="11">Đặt mặt vào khung</text>
<text x="160" y="233" font-size="9">Nhìn thẳng • Hai mắt ngang vạch đứt</text>
</g></svg><svg id="boxes" viewBox="0 0 320 240" aria-label="Detected faces"></svg></div><p>Đưa mặt vào khung xanh dương, tiến lại gần để mặt gần đầy hình bầu dục.</p>
<p id="quality" role="status">Đưa mặt lại gần và nhìn thẳng camera.</p>
<p id="recognition">Đang chờ khuôn mặt...</p>
<div class="enroll" id="enrollment"><form id="register"><input id="name" placeholder="Tên người đăng ký" maxlength="80" required autocomplete="off"><button id="enroll" type="submit">Đăng ký khuôn mặt</button></form><p id="message" role="status"></p><small>Đưa một mặt đủ sáng vào khung hình rồi bấm đăng ký. Cùng tên sẽ thêm mẫu mới (tối đa 20 mẫu).</small><p id="people"></p></div>
<script>
document.querySelector('#camera').src='/stream';
const overlay=document.querySelector('#boxes');
const svgNS='http://www.w3.org/2000/svg';
function drawBoxes(f,stale,recognition){
 overlay.replaceChildren();
 if(stale || !f || !Array.isArray(f.boxes)) return;
 const w=f.image_width || 320,h=f.image_height || 240;
 overlay.setAttribute('viewBox',`0 0 ${w} ${h}`);
 const color=f.quality && !f.quality.accepted ? '#ffb454' : '#4dff88';
 for(const box of f.boxes){
  if(![box.x1,box.y1,box.x2,box.y2,box.score].every(Number.isFinite)) continue;
  const x=Math.max(0,Math.min(w,box.x1)),y=Math.max(0,Math.min(h,box.y1));
  const bw=Math.min(w,box.x2)-x,bh=Math.min(h,box.y2)-y;
  if(bw<=0 || bh<=0) continue;
  const rect=document.createElementNS(svgNS,'rect');
  for(const [k,v] of Object.entries({x,y,width:bw,height:bh,fill:'none',stroke:color,'stroke-width':2})) rect.setAttribute(k,v);
  overlay.appendChild(rect);
  const label=document.createElementNS(svgNS,'text');
  for(const [k,v] of Object.entries({x,y:Math.max(12,y-4),fill:color,'font-size':11,stroke:'#111','stroke-width':2,'paint-order':'stroke'})) label.setAttribute(k,v);
  label.textContent=f.quality && !f.quality.accepted ? 'Chưa đạt chất lượng' : recognition?.status==='matched' ? recognition.name : `Face ${Math.round(box.score*100)}%`;
  overlay.appendChild(label);
 }
}
document.querySelector('#register').addEventListener('submit',async event=>{
 event.preventDefault();
 const button=document.querySelector('#enroll'),message=document.querySelector('#message');
 button.disabled=true;message.textContent='Đang chờ embedding mới, hãy giữ mặt trước camera...';
 try{
  const response=await fetch('/enroll',{method:'POST',headers:{'Content-Type':'application/json','X-Camera-Viewer':'1'},body:JSON.stringify({name:document.querySelector('#name').value})});
  const result=await response.json();
  message.textContent=response.ok ? `Đã đăng ký ${result.person.name} (${result.person.samples} mẫu). Nếu dùng hybrid, giữ server và Wi-Fi hoạt động để cập nhật mẫu xuống bo (khoảng 30 giây).` : result.error;
 }catch(error){message.textContent='Mất kết nối viewer. Vui lòng thử lại.';}
 finally{button.disabled=false;}
});
let busy=false;
async function refresh(){if(busy)return;busy=true;try{
const s=await(await fetch('/status')).json();
document.querySelector('#status').textContent=s.error || (s.age > 3 ? 'Camera stalled - check USB' : s.frames ? 'USB | 320 x 240 | '+s.fps+' FPS | Last frame: '+s.age+' s ago' : 'Waiting for camera over USB...');
const f=s.face;
const stale=!!s.error || s.age===null || s.age>2 || s.face_age===null || s.face_age>2;
drawBoxes(f,stale,s.recognition);
const r=s.recognition;
const q=f?.quality;
const qualityMessages={too_far:'Đưa mặt lại gần camera hơn',cropped:'Lùi nhẹ, giữ toàn bộ khuôn mặt trong ảnh',tilted:'Giữ đầu thẳng, không nghiêng sang vai',turned:'Nhìn thẳng camera, không quay mặt sang bên',pitch:'Đặt camera ngang tầm mắt, không cúi/ngửa',low_confidence:'Nhìn thẳng và tăng ánh sáng',invalid_landmarks:'Để camera thấy rõ mắt, mũi và miệng',embedding_failed:'Giữ mặt yên rồi thử lại',no_face:'Đưa một khuôn mặt vào camera',multiple_faces:'Chỉ một khuôn mặt trong khung hình'};
const qualityText=document.querySelector('#quality');
qualityText.textContent=stale ? 'Đang chờ camera...' : !q ? 'Firmware chưa gửi kiểm tra chất lượng mặt' : q.accepted ? 'Khuôn mặt đạt điều kiện: đủ gần, nhìn thẳng' : qualityMessages[q.status] || 'Khuôn mặt chưa đạt điều kiện';
qualityText.style.color=q?.accepted && !stale ? '#4dff88' : '#ffb454';
if(q && !stale && f.faces===1) qualityText.textContent+=` | Rộng ${Math.round(q.width_ratio*100)}% / tối thiểu ${Math.round(q.min_width_ratio*100)}% | Cao ${Math.round(q.height_ratio*100)}% / tối thiểu ${Math.round(q.min_height_ratio*100)}% | Nghiêng ${Math.abs(q.roll_degrees).toFixed(1)}° / tối đa ${q.max_roll_degrees}°`;
document.querySelector('#recognition').textContent=stale ? 'Đang chờ dữ liệu mới từ camera...' : r?.status==='matched' ? `Đã nhận diện: ${r.name} | cosine ${r.similarity}` : r?.status==='unknown' ? `Chưa nhận diện | cosine ${r.similarity}` : r?.reason || 'Đang chờ khuôn mặt...';
const mode=s.hybrid ? (r?.source==='server' ? 'Server' : 'Edge / ESP32') : 'Máy tính';
if(!stale) document.querySelector('#recognition').textContent+=' | '+mode;
if(s.hybrid && r?.pending_events) document.querySelector('#recognition').textContent+=` | Chờ đồng bộ: ${r.pending_events}`;
if(r?.event_status==='saved') document.querySelector('#recognition').textContent+=' | Đã lưu chấm công';
if(r?.event_status==='storage_error') document.querySelector('#recognition').textContent+=' | LỖI: chưa lưu được chấm công';
document.querySelector('#enrollment').hidden=!s.enrollment_enabled;
document.querySelector('#people').textContent=(s.hybrid ? 'Danh sách trên máy tính (bo cập nhật qua server): ' : 'Đã đăng ký: ')+(s.people.map(p=>`${p.name} (${p.samples} mẫu)`).join(', ') || 'Chưa có');
document.querySelector('#ai').textContent=!f ? 'Waiting for MBF_S8_V1...' : s.face_age>5 ? 'AI result stale - check device' : f.faces===0 ? 'MBF_S8_V1: No face detected' : f.faces>1 ? 'Show only one face' : 'MBF_S8_V1 | Embedding: '+f.dimensions+' dimensions | Detect: '+f.detect_ms+' ms | Embed: '+f.embedding_ms+' ms';
}catch(e){overlay.replaceChildren();document.querySelector('#status').textContent='Viewer disconnected';document.querySelector('#recognition').textContent='Mất kết nối viewer';}finally{busy=false;}}
setInterval(refresh,250);refresh();
</script>'''.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyACM0')
    parser.add_argument('--http-port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1', help='Listen address; use computer Wi-Fi IP for LAN viewing')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--database', default=str(Path(__file__).with_name('faces.json')), help='Local face database JSON file')
    parser.add_argument('--threshold', type=float, default=0.5, help='Minimum cosine similarity, default 0.5')
    parser.add_argument('--match-margin', type=float, default=0.05, help='Minimum gap between the two best identities')
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or not -1 <= args.threshold <= 1:
        parser.error('--threshold must be between -1 and 1')
    if not math.isfinite(args.match_margin) or not 0 <= args.match_margin <= 2:
        parser.error('--match-margin must be between 0 and 2')
    try:
        database = FaceDatabase(args.database)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f'Cannot load face database: {exc}')
    try:
        expose_embeddings = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        expose_embeddings = False
    access_key = secrets.token_urlsafe(12) if not expose_embeddings else None
    authorization = ('Basic ' + base64.b64encode(('camera:' + access_key).encode()).decode()) if access_key else None
    state = dict(frame=None, frames=0, received=0., error='', face=None, face_received=0., face_sequence=0, usb_generation=0, hybrid=False, match=None, match_received=0.)
    lock = threading.Lock()
    changed = threading.Condition(lock)
    timestamps = deque(maxlen=120)
    stop = threading.Event()

    def receive():
        last_error = None
        while not stop.is_set():
            device = None
            pending = bytearray()
            try:
                device = serial.Serial(args.port, 115200, timeout=0.1, exclusive=True)
                print(f'USB opened: {args.port}; waiting for frames', flush=True)
                while not stop.is_set():
                    pending.extend(device.read(max(1, min(device.in_waiting, 65536))))
                    while b'\n' in pending:
                        line, _, rest = pending.partition(b'\n')
                        pending = bytearray(rest)
                        if line.startswith(b'@MATCH:'):
                            try:
                                result = json.loads(line[7:])
                                if not isinstance(result, dict) or result.get('source') not in ('edge', 'server'):
                                    continue
                                if result.get('status') not in ('matched', 'unknown', 'empty', 'unavailable'):
                                    continue
                            except (ValueError, TypeError):
                                continue
                            with lock:
                                state.update(hybrid=True, match=result, match_received=time.monotonic())
                            continue
                        if line.startswith(b'@FACE:'):
                            try:
                                face = json.loads(line[6:])
                                if not isinstance(face, dict) or face.get('model') != 'MBF_S8_V1':
                                    continue
                                if len(face.get('embedding', [])) != face.get('dimensions'):
                                    continue
                            except (ValueError, TypeError):
                                continue
                            with lock:
                                state.update(face=face, face_received=time.monotonic(), face_sequence=state['face_sequence']+1)
                                changed.notify_all()
                            continue
                        if b'face_ai:' in line or b'camera_stream:' in line or b'Guru Meditation' in line or b'hybrid:' in line:
                            print(line.decode(errors='replace'), flush=True)
                        if not line.startswith(b'@CAMERA:'):
                            continue
                        try:
                            data = base64.b64decode(line[8:].strip(), validate=True)
                        except (ValueError, binascii.Error):
                            continue
                        if not (data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')):
                            continue
                        with lock:
                            state.update(frame=data, frames=state['frames']+1,
                                         received=time.monotonic(), error='')
                            count = state['frames']
                            timestamps.append(state['received'])
                            changed.notify_all()
                        if count == 1:
                            print(f'First JPEG received: {len(data)} bytes', flush=True)
                    if len(pending) > 2_000_000:
                        pending.clear()
            except (serial.SerialException, OSError) as exc:
                message = f'USB disconnected: {exc}. Đang tự kết nối lại; kiểm tra cáp USB/nguồn bo.'
                with changed:
                    state.update(error=message, frame=None, face=None, received=0., face_received=0.,
                                 usb_generation=state['usb_generation']+1, match=None, match_received=0.)
                    timestamps.clear()
                    changed.notify_all()
                if message != last_error:
                    print(message, flush=True)
                    last_error = message
            finally:
                if device is not None:
                    try:
                        device.close()
                    except OSError:
                        pass
            stop.wait(1)

    class Handler(BaseHTTPRequestHandler):
        def json_response(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            self.connection.settimeout(10)
            # LAN viewing remains read-only; a custom header prevents cross-origin form writes.
            if not expose_embeddings or self.headers.get('X-Camera-Viewer') != '1':
                self.json_response(403, dict(error='Đăng ký chỉ được phép ở chế độ localhost.'))
                return
            if self.path != '/enroll':
                self.json_response(404, dict(error='Not found'))
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Yêu cầu đăng ký không hợp lệ.')
                payload = json.loads(self.rfile.read(length))
                name = payload.get('name') if isinstance(payload, dict) else None
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
                    raise ValueError('Tên phải có từ 1 đến 80 ký tự, không chứa ký tự điều khiển.')
                with changed:
                    sequence = state['face_sequence']
                    generation = state['usb_generation']
                    ready = changed.wait_for(lambda: state['face_sequence'] > sequence or state['usb_generation'] != generation or state['error'] or stop.is_set(), timeout=8)
                    if not ready or state['usb_generation'] != generation or state['error'] or stop.is_set() or time.monotonic()-state['received'] > 2 or time.monotonic()-state['face_received'] > 2:
                        raise ValueError('Không nhận được embedding mới. Kiểm tra USB và camera rồi thử lại.')
                    face = state['face']
                person = database.enroll(name, face)
            except (ValueError, TypeError) as exc:
                self.json_response(400, dict(error=str(exc)))
                return
            except OSError as exc:
                print(f'Database write failed: {exc}', flush=True)
                self.json_response(500, dict(error='Không lưu được file database. Kiểm tra quyền ghi và dung lượng.'))
                return
            self.json_response(200, dict(person=person))

        def do_GET(self):
            if authorization and not secrets.compare_digest(self.headers.get('Authorization', ''), authorization):
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="ESP32 Camera"')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.connection.settimeout(5)
            path = self.path.split('?')[0]
            if path == '/stream':
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                last = 0
                try:
                    while not stop.is_set():
                        with changed:
                            changed.wait_for(lambda: state['frames'] != last or stop.is_set(), timeout=2)
                            if stop.is_set():
                                break
                            frame, count = state['frame'], state['frames']
                        if frame is None or count == last:
                            continue
                        last = count
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                                         str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                return
            with lock:
                frame = state['frame']
                recent = [t for t in timestamps if time.monotonic() - t < 5]
                fps = round((len(recent)-1)/(recent[-1]-recent[0]), 1) if len(recent)>1 else 0
                face = state['face']
                face_summary = {k: v for k, v in face.items() if k != 'embedding'} if face else None
                status = dict(face=face_summary, face_age=round(time.monotonic()-state['face_received'],1) if face else None, frames=state['frames'], fps=fps, error=state['error'],
                              age=round(time.monotonic()-state['received'], 1) if frame else None)
                hybrid = state['hybrid']
                match = state['match']
                match_age = time.monotonic()-state['match_received']
            fresh = not status['error'] and status['age'] is not None and status['age'] <= 2 and status['face_age'] is not None and status['face_age'] <= 2
            status.update(recognition=database.recognize(face, args.threshold, args.match_margin) if fresh and not hybrid else dict(status='unavailable', reason='Đang chờ dữ liệu camera mới.'),
                          people=database.people(), enrollment_enabled=expose_embeddings)
            if hybrid:
                status['recognition'] = match if fresh and match_age <= 3 else dict(status='unavailable', reason='Đang chờ kết quả từ ESP32.')
            quality = face.get('quality') if face else None
            if fresh and isinstance(quality, dict) and quality.get('accepted') is not True:
                status['recognition'] = dict(status='unavailable', source='edge' if hybrid else 'computer', reason=QUALITY_MESSAGES.get(quality.get('status'), 'Khuôn mặt chưa đạt chất lượng.'))
            status['hybrid'] = hybrid
            if path == '/':
                code, mime, body = 200, 'text/html; charset=utf-8', PAGE
            elif path == '/frame.jpg':
                code, mime, body = (200, 'image/jpeg', frame) if frame else (503, 'text/plain', b'Waiting for camera')
            elif path == '/face':
                if expose_embeddings:
                    code, mime, body = 200, 'application/json', json.dumps(face).encode()
                else:
                    code, mime, body = 403, 'application/json', b'{"error":"Embedding access disabled in LAN mode"}'
            elif path == '/status':
                code, mime, body = 200, 'application/json', json.dumps(status).encode()
            else:
                code, mime, body = 404, 'text/plain', b'Not found'
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    try:
        server = ThreadingHTTPServer((args.host, args.http_port), Handler)
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        browser_host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
        url = f'http://{browser_host}:{args.http_port}'
        print(f'Camera viewer: {url}', flush=True)
        print(f'Face database: {database.path} | cosine threshold: {args.threshold} | margin: {args.match_margin}', flush=True)
        if access_key:
            print(f'LAN login: camera / {access_key}', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        stop.set()
        with changed:
            changed.notify_all()
        if 'reader' in locals():
            reader.join(timeout=2)


if __name__ == '__main__':
    main()
