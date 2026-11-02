import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from face_database import FaceDatabase
from hybrid_gallery import gallery, HEADER, RECORD
from hybrid_server import create_app
from fastapi.testclient import TestClient
import sqlite3


def face(index=0):
    v = [0.]*512
    v[index] = 1.
    return dict(model='MBF_S8_V1', faces=1, dimensions=512, embedding=v)


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root/'faces.json'
        self.db = FaceDatabase(self.db_path)
        self.db.enroll('An', face())
        self.db.data['people'][0]['id'] = 'person-a'
        self.db_path.write_text(json.dumps(self.db.data))

    def test_binary_native_edge_matches_server_and_rejects_corruption(self):
        for _ in range(6):
            self.db.enroll('An', face())
        binary, trimmed, revision = gallery(self.db)
        magic, rev, threshold, margin, count = HEADER.unpack_from(binary)
        self.assertEqual((magic, count), (b'MBF512V1', 5))
        self.assertEqual(len(binary), HEADER.size+5*RECORD.size)
        self.assertEqual(trimmed.recognize(face())['id'], 'person-a')
        self.assertEqual(rev.decode(), revision)
        self.assertAlmostEqual(threshold, .5)
        compiler = shutil.which('c++')
        self.assertIsNotNone(compiler)
        project = Path(__file__).resolve().parents[1]
        executable = self.root/'match-check'
        subprocess.run([compiler, '-std=c++17', '-I'+str(project/'main'), str(project/'tests/hybrid_match_check.cpp'), '-o', str(executable)], check=True, capture_output=True)
        gallery_file = self.root/'gallery.bin'
        gallery_file.write_bytes(binary)
        subprocess.run([str(executable), str(gallery_file)], check=True, capture_output=True)
        other, _, next_revision = gallery(self.db, threshold=.6)
        self.assertNotEqual(revision, next_revision)

    def test_limits_and_wrong_model_dimensions(self):
        self.db.data['people'][0]['samples'][0]['embedding'] = [1., 0.]
        with self.assertRaises(ValueError):
            gallery(self.db)
        for value in [float('nan'), 2]:
            with self.assertRaises(ValueError):
                gallery(FaceDatabase(self.root/'empty'), threshold=value)

    def test_http_matching_gallery_auth_and_idempotent_attendance(self):
        events = self.root/'events.sqlite3'
        app = create_app(self.db_path, events, 'test-token')
        with TestClient(app) as client:
            headers={'Authorization':'Bearer test-token'}
            self.assertEqual(client.get('/health').json()['framework'],'FastAPI')
            self.assertEqual(client.get('/docs').status_code,200)
            self.assertIn('HTTPBearer',client.get('/openapi.json').json()['components']['securitySchemes'])
            self.assertEqual(client.get('/v1/gallery').status_code,401)
            self.assertEqual(client.get('/v1/gallery',headers={'Authorization':'Bearer wrong'}).status_code,401)
            response=client.get('/v1/gallery',headers=headers)
            self.assertEqual(response.content,gallery(self.db)[0])
            self.assertEqual(int(response.headers['content-length']),len(response.content))
            match=client.post('/v1/recognize',json=face(),headers=headers).json()
            self.assertEqual((match['name'],match['source']),('An','server'))
            self.assertEqual(client.post('/v1/recognize',json=face(511),headers=headers).json()['status'],'unknown')
            event=dict(event_id='device-boot-1',device_id='device',employee_id=match['id'],gallery_revision=match['gallery_revision'],source='edge',similarity=.9,timestamp=None,time_quality='unsynced')
            for _ in range(2):
                response=client.post('/v1/events',json=event,headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['ack'],event['event_id'])
            with sqlite3.connect(events) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM events').fetchone()[0],1)
                # Simulate a payload saved by the original server before the migration.
                legacy=dict(event,event_id='old-event',similarity=1)
                connection.execute('INSERT INTO events(event_id,payload) VALUES (?,?)',(legacy['event_id'],json.dumps(legacy,sort_keys=True,allow_nan=False)))
                connection.commit()
            self.assertEqual(client.post('/v1/events',json=legacy,headers=headers).status_code,200)
            self.assertEqual(client.post('/v1/events',json=dict(event,employee_id='other'),headers=headers).status_code,409)
            self.db.enroll('Binh',face(1))
            self.assertEqual(client.post('/v1/recognize',json=face(1),headers=headers).json()['name'],'Binh')
            self.assertEqual(client.get('/v1/gallery',headers=headers).content,gallery(self.db)[0])
            self.assertEqual(client.post('/v1/recognize',json=dict(face(),embedding=[1.]),headers=headers).status_code,422)
            invalid=dict(face()); invalid['embedding'][0]=float('nan')
            self.assertEqual(client.post('/v1/recognize',content=json.dumps(invalid),headers=dict(headers,**{'Content-Type':'application/json'})).status_code,422)
            self.assertEqual(client.post('/v1/events',json=dict(event,similarity=True),headers=headers).status_code,422)
            self.assertEqual(client.post('/v1/events',content=json.dumps(dict(event,event_id='invalid-json-number',extra=float('nan'))),headers=dict(headers,**{'Content-Type':'application/json'})).status_code,400)
            self.assertEqual(client.post('/v1/events',content=b'x'*32769,headers=headers).status_code,413)
            self.db_path.write_text('{broken')
            self.assertEqual(client.get('/v1/gallery',headers=headers).status_code,503)
            self.assertEqual(client.post('/v1/recognize',json=face(),headers=headers).status_code,503)
