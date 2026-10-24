"""Windows Explorer-equivalent launcher regression; no browser or external model calls."""
from pathlib import Path
import os
import subprocess
import sys
import socket
import time
import tempfile
import json
from urllib import request
import threading
import unittest

from .server import make_server

APP=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='win32','Windows launcher requires cmd.exe')
class LauncherTests(unittest.TestCase):
    def invoke(self,*args):
        return subprocess.run([os.environ['ComSpec'],'/d','/c',str(APP/'启动闪光点.cmd'),*args],
                              cwd=APP.parent,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,timeout=20)

    def test_actual_batch_entry_reaches_python_diagnostics(self):
        result=self.invoke('-Check','-NoOpen')
        text=result.stdout.decode('utf-8',errors='replace')
        self.assertEqual(result.returncode,0,text)
        self.assertIn('SPARK_PYTHON_READY',text)

    def test_cold_start_from_another_working_directory_serves_the_app(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as cwd:
            child=subprocess.Popen([os.environ['ComSpec'],'/d','/c',str(APP/'启动闪光点.cmd'),
                                    '-Port',str(port),'-NoOpen'],cwd=cwd,stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    if child.poll() is not None:
                        self.fail(child.stdout.read().decode('utf-8',errors='replace'))
                    try:
                        with request.urlopen(f'http://127.0.0.1:{port}/api/session',timeout=.5) as response:
                            self.assertEqual(json.load(response)['service'],'spark-fusion')
                        return
                    except OSError:
                        time.sleep(.1)
                self.fail('Launcher did not start the local service')
            finally:
                # Only terminate the process tree created by this test, with browser opening disabled.
                if child.poll() is None:
                    subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                child.communicate(timeout=10)

    def test_existing_app_is_reused_without_opening_a_browser(self):
        server=make_server(0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            result=self.invoke('-Port',str(server.server_port),'-NoOpen')
            self.assertEqual(result.returncode,0,result.stdout.decode('utf-8',errors='replace'))
            self.assertIn(b'SPARK_APP_REUSED',result.stdout)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_conflicting_service_reports_failure_instead_of_silent_exit(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Other(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(404);self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Other)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            result=self.invoke('-Port',str(server.server_port),'-NoOpen')
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'SPARK_PORT_IN_USE',result.stdout)
            self.assertIn(b'Launch failed',result.stdout)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
