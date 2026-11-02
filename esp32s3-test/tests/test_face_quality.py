import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from face_database import FaceDatabase

ROOT = Path(__file__).resolve().parents[1]


class QualityTests(unittest.TestCase):
    def test_native_quality_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'check-quality'
            compile_result=subprocess.run([shutil.which('c++'), '-std=c++17', '-I'+str(ROOT/'main'), str(ROOT/'tests/face_quality_check.cpp'), '-o', str(executable)], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode,0,compile_result.stderr)
            result=subprocess.run([str(executable)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_quality_configuration_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            quality=json.loads((ROOT/'face_quality.json').read_text())
            path=folder/'quality.json'
            command=[sys.executable,str(ROOT/'tools/prepare_hybrid.py'),'--database',str(folder/'faces.json'),'--config',str(folder/'config.json'),'--quality',str(path),'--output',str(folder/'generated')]
            path.write_text(json.dumps(quality))
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((folder/'generated/face_quality_settings.h').exists())
            quality['max_roll_degrees']=-1
            path.write_text(json.dumps(quality))
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Invalid quality threshold',result.stderr)

    def test_rejected_quality_never_registers_or_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            db=FaceDatabase(Path(directory)/'faces.json')
            valid=dict(model='MBF_S8_V1',faces=1,dimensions=2,embedding=[1.,0.])
            db.enroll('An',valid)
            rejected=dict(valid,quality=dict(accepted=False,status='too_far'))
            with self.assertRaisesRegex(ValueError,'lại gần'):
                db.enroll('Binh',rejected)
            self.assertEqual(db.recognize(rejected)['status'],'unavailable')
            self.assertEqual(len(db.people()),1)
