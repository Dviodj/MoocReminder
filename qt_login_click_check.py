import json
import threading
from pathlib import Path
from qt_desktop import QApplication, Window, Store, QTimer
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
window=Window(Store(Path(__file__).parent/'qt-login-click-check'))
window.show()
finished=False
checking=False
evidence={}
def report():
    global finished
    if finished:return
    finished=True
    state=window.controller.snapshot()
    result={key:state[key] for key in ('busy','job','phase','errors')}
    result.update(login_button_enabled=window.login.isEnabled(),scan_button_enabled=window.scan.isEnabled(),login_contexts=len(window.manager._contexts),evidence=evidence)
    Path('qt-login-click-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    window.close()
def poll():
    global checking
    if window.controller.job=='error':report()
    if window.controller.job=='waiting_login' and not checking:
        checking=True
        def inspect():
            try:
                page=window.manager._contexts[-1].pages[0]
                evidence.update(page.evaluate_expression('({title:document.title,origin:location.origin,body_present:document.body.innerText.length>100})'))
            except Exception as exc:evidence['error']=str(exc)
            evidence['finished']=True
        threading.Thread(target=inspect,daemon=True).start()
    if evidence.get('finished'):report()
timer=QTimer()
timer.timeout.connect(poll)
timer.start(200)
QTimer.singleShot(400,window.login.click)
QTimer.singleShot(20000,report)
app.exec()
