# tests for v0.9.102.6+ - per-bubble MCP tools-used chips (chat).  
import sys  
sys.path.insert(0, 'C:/Users/humphery/coding/qdrant-rag-agent') 
def _read(name):  
    return open(ROOT + name, encoding='utf-8').read() 
ROOT = 'C:/Users/humphery/coding/qdrant-rag-agent/' 
def _read(name): 
    return open(ROOT + name, encoding='utf-8').read() 
def test_gui_bridge_forwards_tools_used(): 
    from unittest.mock import MagicMock 
    from gui.api import Api 
    api = Api.__new__(Api) 
    agent = MagicMock() 
    agent.chat_turn.return_value = ('ans', 'ctx', []) 
    agent.last_tool_dispatches = ['web_search', 'get_current_time', 'web_search'] 
    api._get_agent = lambda: agent 
    result = api.chat_turn('q', history=[], web_search=False) 
    assert result['tools_used'] == ['web_search', 'get_current_time', 'web_search'] 
def test_tools_used_empty_for_non_list(): 
    from unittest.mock import MagicMock 
    from gui.api import Api 
    api = Api.__new__(Api) 
    agent = MagicMock() 
    agent.chat_turn.return_value = ('ans', 'ctx', []) 
    api._get_agent = lambda: agent 
    result = api.chat_turn('q', history=[], web_search=False) 
    assert result['tools_used'] == [] 
def test_source_guards(): 
    js = _read('gui/assets/app.js') 
    assert 'function appendChatMsg(role, content, sources, startTs, usage, toolsUsed)' in js 
    assert 'msg.toolsUsed = toolsUsed' in js
    # v0.9.102.9 — the 102.6 patch dropped the v0.9.12.0 usage storage;
    # BOTH fields must persist (these guards live in an always-run
    # file because test_chat_time_bar skips whole when jsdom is absent).
    assert 'if (usage) msg.usage = usage;' in js
    assert 'msg.startTs = startTs' in js
    assert '.filter(Boolean)' in js  # v0.9.102.8: "" is not a function fix
    assert ".filter('')" not in js   # the live-bug ban (TypeError on filter)
    assert js.count('function _isLocalImgPath') == 1  # no duplicate defs
    # v0.9.103.0 — the chip toggle keeps the scroll anchored to the
    # toggled bubble (no stick-to-bottom yank).
    assert 'row.dataset.midx = String(mIdx);' in js
    assert 'function renderChat(peekIdx, peekDelta) {' in js
    assert 'renderChat(mIdx, deltaTop);' in js
    assert ".chat-msg-row[data-midx=\"" in js
    assert 'const cur = el.getBoundingClientRect().top - lr.top;' in js
    assert 'log.scrollTop = log.scrollTop + (cur - prev);' in js 
    assert 'function _chatToolsText(m) {' in js 
    assert 'const toolsPeek = new Set();' in js 
    assert 'String.fromCodePoint(0x1F9F0) + ' in js 
    assert 'btn-msg-tools' in js 
    assert 'chat-msg-tools' in js 
    assert 'toolsPeek.has(pkKey)' in js 
    assert 'res.usage, res.tools_used)' in js
def test_api_plain_string_filter_guard(): 
    src = _read('gui/api.py') 
    assert 'tools_used = [str(x) for x in tools_used if x]' in src 
def test_css_and_i18n_present(): 
    css = _read('gui/assets/style.css') 
    assert '.btn-msg-tools {' in css 
    assert '.chat-msg-tools {' in css 
    js = _read('gui/assets/app.js') 
    assert js.count('chatTools:') == 4 
print('TTF_DONE') 
