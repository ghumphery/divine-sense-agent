# tests for the exec script library (v0.9.102.5+) - core, auto-save  
# ring, conda/docker backend assembly (fully mocked - nothing real is  
# pulled), host surface + autosave stamp, stdio parity and the  
# system-prompt teaching block.  
import sys  
sys.path.insert(0, 'C:/Users/humphery/coding/qdrant-rag-agent')  
import os  
import pytest  
nl = chr(10)  
sq = chr(39)  
BIG = 'x' * 250 
@pytest.fixture(autouse=True)  
def _wipe_env(monkeypatch):  
    for v in ('QDRANT_SCRIPT_RUNTIME', 'QDRANT_SCRIPT_CONDA_ENV', 'QDRANT_SCRIPT_DOCKER_IMAGE', 'QDRANT_SCRIPT_DOCKER_NETWORK', 'QDRANT_SCRIPT_DOCKER_MEM', 'QDRANT_SCRIPT_DOCKER_CPUS', 'QDRANT_EXEC_SCRIPT_LIBRARY'):  
        monkeypatch.delenv(v, raising=False)  
@pytest.fixture  
def sb(tmp_path):  
    return tmp_path  
class TestCore: 
    def test_save_list_run(self, sb):  
        from src.exec_tools import save_script, run_script, list_scripts  
        code = 'import sys' + nl + 'print(sum([1, 2, 3]))'  
        r1 = save_script(code, name='t1.py', scripts_root=str(sb))  
        assert r1.get('ok') and r1.get('name') == 't1.py'  
        r2 = run_script('t1.py', cwd=str(sb), scripts_root=str(sb))  
        assert r2.get('exit_code') == 0 and '6' in r2.get('stdout', '')  
        assert r2.get('run_count') == 1  
        ls = list_scripts(scripts_root=str(sb))  
        assert ls.get('total') == 1 and ls['scripts'][0]['name'] == 't1.py'  
    def test_lang_detect(self):  
        from src.exec_tools import detect_script_lang  
        assert detect_script_lang('auto', 'import os' + nl + 'print(1)') == 'py'  
        assert detect_script_lang('auto', '$v = 1' + nl + 'Write-Output $v') == 'ps1'  
        assert detect_script_lang('powershell', 'x') == 'ps1'  
        assert detect_script_lang('CMD', 'dir') == 'cmd'  
        assert detect_script_lang('auto', 'dir') == 'py'  
        assert detect_script_lang('not-a-lang', 'import x') == 'py' 
    def test_slug(self):  
        from src.exec_tools import _script_file_name  
        assert _script_file_name('K Line.Report', 'py') == 'k-line-report.py'  
        assert _script_file_name('a.py', 'py') == 'a.py'  
        assert _script_file_name('a.sh', 'py') == 'a.py'  
        assert _script_file_name('', 'py') == 'script.py'  
    def test_dedup_same_code(self, sb):  
        from src.exec_tools import save_script  
        r1 = save_script(BIG, name='aa', scripts_root=str(sb))  
        r2 = save_script(BIG, name='bb', scripts_root=str(sb))  
        assert r2.get('duplicate') and r2.get('name') == 'aa.py'  
        files = list((sb / 'scripts').iterdir())  
        assert len(files) == 2  
    def test_name_collision_versioned(self, sb):  
        from src.exec_tools import save_script  
        c1 = 'print(' + sq + 'one' + sq + ')'  
        c2 = 'print(' + sq + 'two' + sq + ')'  
        assert save_script(c1, name='kk', scripts_root=str(sb)).get('name') == 'kk.py'  
        r = save_script(c2, name='kk', scripts_root=str(sb))  
        assert r.get('name') == 'kk-2.py' 
    def test_blocklist_refused(self, sb):  
        from src.exec_tools import save_script  
        code = 'import os' + nl + 'os.system(' + sq + 'shutdown now' + sq + ')'  
        r = save_script(code, name='bad', scripts_root=str(sb))  
        assert 'refused' in r.get('error', '')  
    def test_tamper_refused(self, sb):  
        from src.exec_tools import save_script, run_script  
        assert save_script(BIG, name='tt', scripts_root=str(sb)).get('ok')  
        p = sb / 'scripts' / 'tt.py'  
        p.write_bytes(b'import sys' + bytes([10]) + b'print(999)')  
        r = run_script('tt', cwd=str(sb), scripts_root=str(sb))  
        assert 'sha1 mismatch' in r.get('error', '')  
    def test_args_pass_through(self, sb):  
        from src.exec_tools import save_script, run_script  
        code = 'import sys' + nl + 'print(' + sq + ' ' + sq + '.join(sys.argv[1:]))'  
        assert save_script(code, name='argsy', scripts_root=str(sb)).get('ok')  
        r = run_script('argsy', 'aa bb', cwd=str(sb), scripts_root=str(sb))  
        assert r.get('exit_code') == 0 and 'aa bb' in r.get('stdout', '')  
    def test_missing_script(self, sb):  
        from src.exec_tools import run_script  
        r = run_script('zzz', cwd=str(sb), scripts_root=str(sb))  
        assert 'no such script' in r.get('error', '') 
class TestAuto:  
    def test_off_env(self, sb, monkeypatch):  
        from src.exec_tools import auto_save_script  
        monkeypatch.setenv('QDRANT_EXEC_SCRIPT_LIBRARY', 'off')  
        assert auto_save_script(BIG, scripts_root=str(sb)) is None  
    def test_dedup_bumps_runs(self, sb):  
        from src.exec_tools import auto_save_script  
        m1 = auto_save_script(BIG, scripts_root=str(sb))  
        m2 = auto_save_script(BIG, scripts_root=str(sb))  
        assert m1.get('name') == m2.get('name') and m2.get('dedup') and m2.get('runs') == 2  
    def test_ring_prune_keeps_named(self, sb):  
        from src.exec_tools import auto_save_script, save_script, list_scripts  

        save_script(BIG, name='keepme', scripts_root=str(sb))  
        for i in range(51):  
            auto_save_script('y' * 220 + str(i), scripts_root=str(sb))  
        ls = list_scripts(scripts_root=str(sb))  
        names = [r['name'] for r in ls['scripts']]  
        assert 'keepme.py' in names  
        autos = [n for n in names if n.startswith('auto-')]  
        assert len(autos) == 50 

class TestBackends:  
    def test_conda_validation(self, sb):  
        from src.exec_tools import save_script  
        r = save_script(BIG, name='cc', conda_env='bad name', scripts_root=str(sb))  
        assert 'invalid conda_env' in r.get('error', '')  
        r2 = save_script(BIG, name='cc', lang='ps1', conda_env='ok', scripts_root=str(sb))  
        assert 'only applies to python' in r2.get('error', '')  
        r3 = save_script(BIG, name='cc', conda_env='ok', docker_image='python:3.12-slim', scripts_root=str(sb))  
        assert 'ONE isolation' in r3.get('error', '')  
    def test_conda_cmd_shape(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        rec = []  
        monkeypatch.setattr(et.shutil, 'which', lambda x: ('/fake/conda' if x == 'conda' else None))  
        monkeypatch.setattr(et, '_run_capture', lambda cmd, cwd, to, mo, rr: rec.append(cmd) or {'stdout': '', 'stderr': '', 'exit_code': 0})  
        save_script(BIG, name='cn', conda_env='env1', scripts_root=str(sb))  
        r = run_script('cn', cwd=str(sb), scripts_root=str(sb))  
        cmd = rec[-1]  
        assert cmd[0] == '/fake/conda' and cmd[1] == 'run'  
        assert cmd[3] == '-n' and cmd[4] == 'env1' and 'python' in cmd  
        assert r['tool'].startswith('script:py/conda')  
    def test_conda_missing_clean(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        monkeypatch.setattr(et.shutil, 'which', lambda x: None)  
        save_script(BIG, name='cm', conda_env='envx', scripts_root=str(sb))  
        r = run_script('cm', cwd=str(sb), scripts_root=str(sb))  
        assert 'conda not found' in r.get('error', '')  
    def test_global_runtime_conda(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        monkeypatch.setenv('QDRANT_SCRIPT_RUNTIME', 'conda')  
        monkeypatch.setenv('QDRANT_SCRIPT_CONDA_ENV', 'genv')  
        rec = []  
        monkeypatch.setattr(et.shutil, 'which', lambda x: ('/fake/conda' if x == 'conda' else None))  
        monkeypatch.setattr(et, '_run_capture', lambda cmd, cwd, to, mo, rr: rec.append(cmd) or {'stdout': '', 'stderr': '', 'exit_code': 0})  
        save_script(BIG, name='gr', scripts_root=str(sb))  
        run_script('gr', cwd=str(sb), scripts_root=str(sb))  
        assert rec[-1][4] == 'genv' 

    def test_docker_image_validation(self, sb):  
        from src.exec_tools import save_script  
        r = save_script(BIG, name='dd', lang='cmd', docker_image='x:1', scripts_root=str(sb))  
        assert 'docker does not apply' in r.get('error', '')  
        r2 = save_script(BIG, name='dd', docker_image='x y', scripts_root=str(sb))  
        assert 'invalid docker_image' in r2.get('error', '')  
    def test_docker_cmd_shape(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        rec = []  
        monkeypatch.setattr(et.shutil, 'which', lambda x: ('/fake/docker' if x == 'docker' else None))  
        monkeypatch.setattr(et, '_run_capture', lambda cmd, cwd, to, mo, rr: rec.append(cmd) or {'stdout': '', 'stderr': '', 'exit_code': 0})  
        save_script(BIG, name='dk', docker_image='py:3.12', scripts_root=str(sb))  
        r = run_script('dk', 'v1 v2', cwd=str(sb), scripts_root=str(sb))  
        cmd = rec[-1]  
        assert cmd[0] == '/fake/docker' and '--rm' in cmd  
        assert cmd[cmd.index('--network') + 1] == 'none'  
        assert cmd[cmd.index('-v') + 1].endswith(':/sandbox')  
        assert cmd[cmd.index('--memory') + 1] == '2g' and cmd[cmd.index('--cpus') + 1] == '2'  
        pi = cmd.index('python')
        assert cmd[pi + 1].endswith('/sandbox/scripts/dk.py') and cmd[pi + 2] == 'v1'  
        assert r['tool'].startswith('script:py/docker') 
    def test_docker_bridge_network(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        rec = []  
        monkeypatch.setattr(et.shutil, 'which', lambda x: ('/fake/docker' if x == 'docker' else None))  
        monkeypatch.setattr(et, '_run_capture', lambda cmd, cwd, to, mo, rr: rec.append(cmd) or {'stdout': '', 'stderr': '', 'exit_code': 0})  
        save_script(BIG, name='ddb', docker_image='x:1', network='bridge', scripts_root=str(sb))  
        run_script('ddb', cwd=str(sb), scripts_root=str(sb))  
        assert rec[-1][rec[-1].index('--network') + 1] == 'bridge'  
    def test_docker_global_defaults(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        monkeypatch.setenv('QDRANT_SCRIPT_DOCKER_NETWORK', 'bridge')  
        monkeypatch.setenv('QDRANT_SCRIPT_DOCKER_MEM', '4g')  
        rec = []  
        monkeypatch.setattr(et.shutil, 'which', lambda x: ('/fake/docker' if x == 'docker' else None))  
        monkeypatch.setattr(et, '_run_capture', lambda cmd, cwd, to, mo, rr: rec.append(cmd) or {'stdout': '', 'stderr': '', 'exit_code': 0})  
        save_script(BIG, name='dg', docker_image='x:1', scripts_root=str(sb))  
        run_script('dg', cwd=str(sb), scripts_root=str(sb))  
        assert rec[-1][rec[-1].index('--network') + 1] == 'bridge'  
        assert rec[-1][rec[-1].index('--memory') + 1] == '4g'  
    def test_docker_missing_clean(self, sb, monkeypatch):  
        import src.exec_tools as et  
        from src.exec_tools import save_script, run_script  
        monkeypatch.setattr(et.shutil, 'which', lambda x: None)  
        save_script(BIG, name='dm', docker_image='x:1', scripts_root=str(sb))  
        r = run_script('dm', cwd=str(sb), scripts_root=str(sb))  
        assert 'docker not found' in r.get('error', '') 

class TestHost:  
    def test_surface_8_tools(self):  
        from src.mcp_client.host import ExecLocalHost  
        h = ExecLocalHost()  
        names = sorted(x.name for x in h.list_tools())  
        assert len(names) == 8  
        assert 'script_save' in names and 'script_list' in names and 'script_run' in names  
    def test_autosave_stamp_and_replay(self, tmp_path, monkeypatch):  
        monkeypatch.setenv('QDRANT_FILESYSTEM_WRITE_ROOT', str(tmp_path))  
        from src.mcp_client.host import ExecLocalHost  
        h = ExecLocalHost()  
        code = 'import sys' + nl + 's = 0' + nl + 'for i in range(1, 11):' + nl + '    s = s + i' + nl + 'print(s)'  
        code = code + nl + 'pad = ' + sq + 'x' * 220 + sq  
        out = h._run_python({'code': code})  
        assert out.get('exit_code') == 0  
        nm = out.get('auto_saved', {}).get('name', '')  
        assert nm.startswith('auto-')  
        r2 = h._script_run({'name': nm})  
        assert r2.get('exit_code') == 0 and '55' in r2.get('stdout', '')  
        assert h._script_list({}).get('total') == 1  
    def test_no_autosave_on_failure(self, tmp_path, monkeypatch):  
        monkeypatch.setenv('QDRANT_FILESYSTEM_WRITE_ROOT', str(tmp_path))  
        from src.mcp_client.host import ExecLocalHost  
        h = ExecLocalHost()  
        code = 'raise ValueError(' + sq + 'x' + sq + ') ' + sq + 'y' + sq  
        out = h._run_python({'code': code})  
        assert out.get('exit_code') != 0  
        assert 'auto_saved' not in out  
    def test_no_autosave_small_code(self, tmp_path, monkeypatch):  
        monkeypatch.setenv('QDRANT_FILESYSTEM_WRITE_ROOT', str(tmp_path))  
        from src.mcp_client.host import ExecLocalHost  
        h = ExecLocalHost()  
        out = h._run_python({'code': 'print(1+2)'})  
        assert out.get('exit_code') == 0 and 'auto_saved' not in out  
    def test_host_missing_script(self, tmp_path, monkeypatch):  
        monkeypatch.setenv('QDRANT_FILESYSTEM_WRITE_ROOT', str(tmp_path))  
        from src.mcp_client.host import ExecLocalHost  
        h = ExecLocalHost()  
        assert 'no such script' in h._script_run({'name': 'zz'}).get('error', '') 

class TestStdioMirror:  
    def test_parity(self):  
        from src.mcp_server.exec_server import ExecMCPServer  
        from src.mcp_client.host import ExecLocalHost  
        s = ExecMCPServer()  
        specs = sorted(x.name for x in s._tool_specs())  
        names = sorted(x.name for x in ExecLocalHost().list_tools())  
        assert specs == names and len(specs) == 8  
class TestPrompt:  
    def test_block_and_legacy(self):  
        from src.agent.agent import Agent  
        pr = Agent.__new__(Agent)  
        p1 = Agent._system_prompt(pr, tools_enabled=True)  
        assert 'Script library (v0.9.102.5+)' in p1  
        assert 'script_run' in p1 and 'auto_saved' in p1  
        p2 = Agent._system_prompt(pr, tools_enabled=False)  
        assert 'script_run' not in p2 