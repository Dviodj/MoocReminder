import json,time,tempfile,threading
from pathlib import Path
import sys
from qt_bootstrap import prepare_qt
prepare_qt('data')
from PySide6.QtWidgets import QApplication,QWidget
from webview2_reader import Manager
app=QApplication([]);widget=QWidget();widget.show()
result={};manager=Manager(Path(tempfile.gettempdir())/'mooc-adapter-smoke')
def worker():
    started=time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as folder:
            html=Path(folder)/'fixture.html'
            html.write_text('<html><body><h1>native adapter test</h1><button onclick="window.open(\'child.html\')">course</button></body></html>',encoding='utf-8')
            (Path(folder)/'child.html').write_text('<html><body>popup page</body></html>',encoding='utf-8')
            c=manager.context('smoke',False,lambda:False);p=c.new_page();p.goto(html.as_uri())
            result['text']=p.locator('h1').inner_text();result['script']=p.evaluate('()=>({count:document.querySelectorAll("button").length})')
            try:p.evaluate_expression('(()=>{throw new Error("fixture")})()')
            except RuntimeError:result['js_error_detected']=True
            with p.expect_popup() as opened:p.get_by_text('course').click()
            result['popup']=opened.value.locator('body').inner_text();opened.value.close();c.close()
            result['closed_pages']=len(c.pages);result['profile_retained']=c.profile.exists()
            from webview2_reader import Page
            early=Page(c);c.owned.append(early)
            try:manager.bridge.call(early._create,timeout=.001)
            except TimeoutError:result['initialization_timeout_detected']=True
            time.sleep(.5)
            result['initialization_timeout_cleaned']=early.closed and early.form is None and early.view is None
            for i in range(10):
                repeat=manager.context('smoke',False,lambda:False);repeat.close()
            result['retained_contexts']=len(manager._contexts)
            logins=[]
            for account in ('one','one','two','two'):
                login=manager.context(account,True,lambda:False);lp=login.new_page();lp.goto(html.as_uri());logins.append(lp)
            result['previous_login_controls_disposed']=all(p.closed and p.form is None and p.view is None for p in logins[:-1])
            scan=manager.context('two',False,lambda:False);sp=scan.new_page();sp.goto(html.as_uri())
            result['hidden_scan_preserves_login']=not logins[-1].closed
            result['live_visible_contexts']=sum(bool(c.visible and c.pages) for c in manager._contexts)
            result['login_profiles_retained']=all((manager.folder/'webview2-profiles'/account).exists() for account in ('one','two'))
            scan.close();logins[-1].close()
            shutdown_started=time.monotonic();manager.shutdown();result['shutdown_seconds']=round(time.monotonic()-shutdown_started,3);result['total_seconds']=round(time.monotonic()-started,3);result['success']=result['text']=='native adapter test' and result['popup']=='popup page'
    except Exception as exc:
        import traceback
        result['error']=str(exc);result['traceback']=traceback.format_exc()
        try:manager.shutdown()
        except Exception as shutdown:result['shutdown_error']=str(shutdown)
    Path('docs/superpowers/plans/task-2-native-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False),flush=True)
t=threading.Thread(target=worker,daemon=True);t.start()
end=time.monotonic()+65
while t.is_alive() and time.monotonic()<end:app.processEvents();time.sleep(.01)
if t.is_alive():raise RuntimeError('smoke exceeded 65 seconds')
