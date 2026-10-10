import helpers
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipIf(os.name == 'nt', 'GPU launcher requires POSIX')
class GpuScriptTests(unittest.TestCase):
    def test_library_path_and_arguments(self):
        """P14: library folders precede existing paths; arguments stay intact."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root/'call.json'
            stub = helpers.make_stub_tool(root,'python', '''import importlib.util, json, os, sys
from pathlib import Path
from types import SimpleNamespace
if sys.argv[1] == '-c':
    folders = {'nvidia.cublas.lib':'/gpu/blas','nvidia.cudnn.lib':'/gpu/dnn'}
    def locate(name):
        return None if os.environ.get('STUB_GPU_MISSING') else SimpleNamespace(submodule_search_locations=[folders[name]])
    importlib.util.find_spec = locate
    exec(sys.argv[2])
else:
    Path(os.environ['STUB_LOG']).write_text(json.dumps([sys.argv[1:], os.environ['LD_LIBRARY_PATH']]))
''')
            env = dict(os.environ,PYTHON=str(stub),STUB_LOG=str(log),LD_LIBRARY_PATH='/existing')
            command = ['bash',str(helpers.ROOT/'scripts/run_gpu.sh'),'run','talk with spaces.wav','--device','cuda']
            result = subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(log.read_text(encoding='utf-8')),[
                ['-m','channelrecap','run','talk with spaces.wav','--device','cuda'],
                '/gpu/blas:/gpu/dnn:/existing'])
            del env['LD_LIBRARY_PATH']
            result = subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(log.read_text(encoding='utf-8'))[1],'/gpu/blas:/gpu/dnn')
            env['STUB_GPU_MISSING']='1'
            result = subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,3)
            self.assertIn('python -m pip install ".[gpu]"',result.stderr)
