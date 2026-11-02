"""HTTP checks use synthetic serial data and an isolated temporary database."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
FAKE = '''
import json, sys, time, serial
from pathlib import Path
from unittest.mock import patch
import camera_viewer
class Device:
    in_waiting = 0
    def __init__(self, *a, **kw): pass
    def read(self, count):
        time.sleep(.1)
        if Path(sys.argv[sys.argv.index('--database')+1]+'.unplug').exists():
            raise serial.SerialException('simulated disconnect')
        face = dict(model='MBF_S8_V1', faces=1, dimensions=2, embedding=[1.,0.], boxes=[])
        if Path(sys.argv[sys.argv.index('--database')+1]+'.quality').exists():
            face['quality'] = dict(accepted=False, status='too_far')
        output = b'@CAMERA:/9j/2Q==\\n@FACE:' + json.dumps(face).encode() + b'\\n'
        if Path(sys.argv[sys.argv.index('--database')+1]+'.hybrid').exists():
            result = dict(status='matched', source='edge', name='Edge Person', id='edge-id', similarity=.9)
            output += b'@MATCH:'+json.dumps(result).encode()+b'\\n'
        return output
    def close(self): pass
with patch('camera_viewer.serial.Serial', Device):
    camera_viewer.main()
'''


class HttpTests(unittest.TestCase):
    def test_enrollment_matching_and_request_restrictions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'faces.json'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen([sys.executable, '-c', FAKE, '--no-browser', '--http-port', str(port), '--database', str(path)], cwd=ROOT,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                base = f'http://127.0.0.1:{port}'
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base+'/status', timeout=1) as response:
                            if json.load(response)['frames']:
                                break
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(.05)
                else:
                    self.fail('Viewer did not start')
                def enroll(name, header=True):
                    headers = {'Content-Type':'application/json'}
                    if header:
                        headers['X-Camera-Viewer'] = '1'
                    request = urllib.request.Request(base+'/enroll', data=json.dumps({'name':name}).encode(), headers=headers)
                    return urllib.request.urlopen(request, timeout=10)
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    enroll('An', False)
                self.assertEqual(rejected.exception.code, 403)
                self.assertFalse(path.exists())
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    enroll(' ')
                self.assertEqual(invalid.exception.code, 400)
                with enroll('An') as response:
                    self.assertEqual(json.load(response)['person']['samples'], 1)
                with urllib.request.urlopen(base+'/status') as response:
                    status = json.load(response)
                self.assertEqual(status['recognition']['name'], 'An')
                self.assertNotIn('embedding', status['face'])
                self.assertEqual(json.loads(path.read_text())['people'][0]['name'], 'An')
                before = path.read_bytes()
                Path(str(path)+'.unplug').touch()
                for _ in range(40):
                    with urllib.request.urlopen(base+'/status') as response:
                        disconnected = json.load(response)
                    if disconnected['error']:
                        break
                    time.sleep(.05)
                self.assertEqual(disconnected['recognition']['status'], 'unavailable')
                with self.assertRaises(urllib.error.HTTPError) as disconnected_request:
                    enroll('Binh')
                self.assertEqual(disconnected_request.exception.code, 400)
                self.assertEqual(path.read_bytes(), before)
                self.assertIsNone(disconnected['face'])
                Path(str(path)+'.unplug').unlink()
                for _ in range(100):
                    with urllib.request.urlopen(base+'/status') as response:
                        recovered = json.load(response)
                    if recovered['recognition'].get('name') == 'An' and not recovered['error']:
                        break
                    time.sleep(.05)
                else:
                    self.fail('USB did not reconnect automatically')
                self.assertEqual(recovered['people'][0]['samples'], 1)
                with enroll('An') as response:
                    self.assertEqual(json.load(response)['person']['samples'], 2)
                Path(str(path)+'.hybrid').touch()
                for _ in range(40):
                    with urllib.request.urlopen(base+'/status') as response:
                        hybrid = json.load(response)
                    if hybrid.get('hybrid'):
                        break
                    time.sleep(.05)
                self.assertEqual(hybrid['recognition']['name'], 'Edge Person')
                self.assertEqual(hybrid['recognition']['source'], 'edge')
                Path(str(path)+'.quality').touch()
                for _ in range(40):
                    with urllib.request.urlopen(base+'/status') as response:
                        rejected_quality=json.load(response)
                    if rejected_quality.get('face',{}).get('quality'):
                        break
                    time.sleep(.05)
                self.assertEqual(rejected_quality['recognition']['status'],'unavailable')
                self.assertIn('lại gần',rejected_quality['recognition']['reason'])
                before=path.read_bytes()
                with self.assertRaises(urllib.error.HTTPError) as rejected_registration:
                    enroll('Binh')
                self.assertEqual(rejected_registration.exception.code,400)
                self.assertEqual(path.read_bytes(),before)
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
