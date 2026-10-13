from dataclasses import dataclass, field
import fnmatch
import json
from pathlib import Path
import threading
import time
from dom_adapter import Locator, Popup
from version import APP_NAME

@dataclass
class Request:
    event: object = field(default_factory=threading.Event)
    lock: object = field(default_factory=threading.Lock)
    abandoned: bool = False
    result: object = None
    error: object = None
    def finish(self, value=None, error=None):
        with self.lock:
            if self.abandoned or self.event.is_set(): return
            self.result,self.error=value,error;self.event.set()
    def abandon(self):
        with self.lock: self.abandoned=True

class Bridge:
    def __init__(self, post): self.post=post
    def call(self, fn, timeout=15, cancelled=lambda:False):
        if cancelled(): raise InterruptedError('当前操作已取消')
        r=Request()
        def execute():
            if r.abandoned: return
            if cancelled(): return r.finish(error=InterruptedError('当前操作已取消'))
            try: fn(r)
            except Exception as exc: r.finish(error=exc)
        self.post(execute)
        end=time.monotonic()+timeout
        while not r.event.wait(.025):
            if cancelled(): r.abandon();raise InterruptedError('当前操作已取消')
            if time.monotonic()>=end: r.abandon();raise TimeoutError('网页读取超时，请重试。')
        if cancelled(): r.abandon();raise InterruptedError('当前操作已取消')
        if r.error: raise r.error
        return r.result

def decode_script_result(raw):
    try:
        data=json.loads(str(raw))
        if not isinstance(data,dict) or data.get('ok') is not True: raise ValueError()
        return data.get('value')
    except (ValueError,TypeError) as exc:
        raise RuntimeError('网页脚本执行失败，或网页尚未准备好。') from exc

def navigation_error(ok,status):
    return None if ok else RuntimeError('网页加载失败（'+str(status)+'）。')

class Manager:
    def __init__(self,folder,parent=None):
        self.folder=Path(folder);self.on_error=lambda message:None
        self._contexts=[];self._started=False;self._closed=False;self._start_lock=threading.Lock()
    def _start(self):
        with self._start_lock:
            if self._closed: raise RuntimeError('网页组件已关闭')
            if self._started:return
            from webview_runtime import initialize_webview
            initialize_webview()
            from System import Action,String
            from System.Threading import Thread,ThreadStart,ApartmentState
            from System.Threading.Tasks import Task,TaskScheduler
            from System.Windows.Forms import Application,ApplicationContext,Control,Form,DockStyle
            from Microsoft.Web.WebView2.WinForms import WebView2,CoreWebView2CreationProperties
            self.api=locals().copy();ready=threading.Event();self._startup_error=None
            def run():
                try:
                    self.dispatch=Control();self.dispatch.CreateControl();unused=self.dispatch.Handle
                    self.app_context=ApplicationContext()
                    ready.set();Application.Run(self.app_context)
                except Exception as exc:self._startup_error=exc;ready.set()
                finally:
                    if hasattr(self,'dispatch'):self.dispatch.Dispose()
            self.thread=Thread(ThreadStart(run));self.thread.IsBackground=True;self.thread.SetApartmentState(ApartmentState.STA);self.thread.Start()
            if not ready.wait(10):raise TimeoutError('原生网页线程启动超时')
            if self._startup_error:raise self._startup_error
            self.bridge=Bridge(lambda fn:self.dispatch.BeginInvoke(Action(fn)));self._started=True
    def context(self,user_id,visible,cancelled):
        self._start()
        if cancelled():raise InterruptedError('当前操作已取消')
        if visible:
            def retire_login(r):
                for previous in self._contexts:
                    if previous.visible:
                        for page in previous.pages:page._dispose()
                r.finish(True)
            self.bridge.call(retire_login,timeout=4,cancelled=cancelled)
        # Each handle owns its pages; profiles are persistent and shared by account.
        safe=str(user_id or 'default')
        if not safe.replace('-','').replace('_','').isalnum():raise ValueError('账号标识无效')
        self._contexts=[c for c in self._contexts if c.pages]
        c=Context(self,self.folder/'webview2-profiles'/safe,visible,cancelled);self._contexts.append(c);return c
    def shutdown(self):
        if self._closed:return
        self._closed=True
        if not self._started:return
        def stop(r):
            for c in self._contexts:
                for p in list(c.pages):p._dispose()
            self.app_context.ExitThread();r.finish(True)
        try:self.bridge.call(stop,timeout=4)
        finally:
            if not self.thread.Join(4000):raise TimeoutError('原生网页线程未在限定时间内退出')

class Context:
    def __init__(self,manager,profile,visible,cancelled):
        self.manager,self.profile,self.visible,self.cancelled=manager,profile,visible,cancelled
        self.owned=[];self.timeout=15000
    @property
    def pages(self):return list(self.owned)
    def set_default_timeout(self,value):self.timeout=value
    def new_page(self):
        if self not in self.manager._contexts:self.manager._contexts.append(self)
        p=Page(self);self.owned.append(p)
        try:p._call(p._create,timeout=30);return p
        except Exception:
            p.close();raise
    def close(self):
        for p in self.pages:p.close()

class Page:
    def __init__(self,context):
        self.context=context;self.closed=False;self.form=None;self.view=None
        self.load_done=threading.Event();self.load_ok=True;self.failure=None;self._navigation_id=None
    def _call(self,fn,timeout=None):
        if self.closed:raise RuntimeError('网页已关闭')
        return self.context.manager.bridge.call(fn,timeout or self.context.timeout/1000,self.context.cancelled)
    def _create(self,r):
        a=self.context.manager.api
        self.context.profile.mkdir(parents=True,exist_ok=True)
        self.form=a['Form']();self.form.Text=f'登录中国大学 MOOC · {APP_NAME}';self.form.Width=1100;self.form.Height=800
        self.view=a['WebView2']();self.view.Dock=a['DockStyle'].Fill
        props=a['CoreWebView2CreationProperties']();props.UserDataFolder=str(self.context.profile.resolve());self.view.CreationProperties=props
        self.form.Controls.Add(self.view)
        if not self.context.visible:self.form.Opacity=0
        def closing(sender,args):
            if not self.closed:args.Cancel=True;self.form.Hide()
        self.form.FormClosing+=closing
        def initialized(sender,args):
            if r.abandoned or self.closed:
                self._dispose();return
            if not args.IsSuccess:r.finish(error=RuntimeError('原生网页初始化失败'));self._dispose();return
            self.view.CoreWebView2.NavigationStarting+=self._starting
            self.view.CoreWebView2.NavigationCompleted+=self._loaded
            self.view.CoreWebView2.ProcessFailed+=self._process_failed
            r.finish(self)
        self.view.CoreWebView2InitializationCompleted+=initialized
        self.form.Show()
        if not self.context.visible:self.form.Hide()
        self.view.EnsureCoreWebView2Async(None)
    def _starting(self,sender,args):
        if self.closed:return
        self._navigation_id=args.NavigationId;self.failure=None;self.load_done.clear()
    def _loaded(self,sender,args):
        if self.closed or args.NavigationId!=self._navigation_id:return
        self.load_ok=bool(args.IsSuccess);self.failure=navigation_error(self.load_ok,args.WebErrorStatus);self.load_done.set()
        if self.failure and self.context.visible and not self.context.cancelled():
            try:self.context.manager.on_error(str(self.failure))
            except Exception:pass
    def _process_failed(self,sender,args):
        if self.closed:return
        self.failure=RuntimeError('内置网页进程退出（'+str(args.ProcessFailedKind)+'）。请重新连接。')
        self.load_ok=False;self.load_done.set()
        if not self.context.cancelled():
            try:self.context.manager.on_error(str(self.failure))
            except Exception:pass
    @property
    def url(self):return self._call(lambda r:r.finish(str(self.view.CoreWebView2.Source)))
    def evaluate_expression(self,expression):
        script='(()=>{try{return {ok:true,value:('+expression+')};}catch(e){return {ok:false};}})()'
        def run(r):
            if self.failure:return r.finish(error=self.failure)
            a=self.context.manager.api
            def done(task):
                if r.abandoned or self.closed:return
                try:
                    if task.IsFaulted or task.IsCanceled:raise RuntimeError('网页脚本执行失败')
                    r.finish(decode_script_result(task.Result))
                except Exception as exc:r.finish(error=exc)
            task=self.view.CoreWebView2.ExecuteScriptAsync(script)
            task.ContinueWith(a['Action'][a['Task'][a['String']]](done),a['TaskScheduler'].FromCurrentSynchronizationContext())
        return self._call(run)
    def evaluate(self,script):return self.evaluate_expression('('+script+')()')
    def goto(self,url,wait_until='domcontentloaded',timeout=30000):
        current=self.url
        if current and current!='about:blank' and url!='about:blank':self.goto('about:blank',timeout=5000)
        def navigate(r):self.load_done.clear();self.failure=None;self.view.CoreWebView2.Navigate(str(url));r.finish(True)
        self._call(navigate);self.wait_for_load_state(wait_until,timeout)
    def wait_for_load_state(self,state='networkidle',timeout=10000):
        end=time.monotonic()+timeout/1000
        while not self.load_done.wait(.05):
            if self.context.cancelled():raise InterruptedError('当前操作已取消')
            if self.closed:raise RuntimeError('网页已关闭')
            if time.monotonic()>=end:raise TimeoutError('网页加载超时')
        if self.context.cancelled():raise InterruptedError('当前操作已取消')
        if self.failure:raise self.failure
        if state=='networkidle':self.wait_for_timeout(1000)
    def wait_for_timeout(self,milliseconds):
        end=time.monotonic()+milliseconds/1000
        while time.monotonic()<end:
            if self.context.cancelled():raise InterruptedError('当前操作已取消')
            time.sleep(min(.05,max(0,end-time.monotonic())))
    def wait_for_url(self,pattern,timeout=20000):
        end=time.monotonic()+timeout/1000
        while time.monotonic()<end:
            url=self.url
            if (hasattr(pattern,'search') and pattern.search(url)) or (isinstance(pattern,str) and fnmatch.fnmatch(url,pattern)):return
            self.wait_for_timeout(150)
        raise TimeoutError('未能读取课程学习链接')
    def locator(self,selector):return Locator(self,'Array.from(document.querySelectorAll('+json.dumps(selector)+'))')
    def get_by_text(self,text,exact=True):
        predicate='e.innerText.trim()==='+json.dumps(text) if exact else 'e.innerText.includes('+json.dumps(text)+')'
        return Locator(self,'Array.from(document.querySelectorAll("a,button,[role=button]")).filter(e=>'+predicate+')')
    def expect_popup(self,timeout=15000):return Popup(self,timeout)
    def bring_to_front(self):
        self._call(lambda r:(self.form.Show(),setattr(self.form,'Opacity',1),self.form.Activate(),r.finish(True)))
    def _dispose(self):
        self.closed=True;self.load_done.set()
        if self.view:
            try:
                if self.view.CoreWebView2:self.view.CoreWebView2.Stop()
            finally:self.view.Dispose();self.view=None
        if self.form:self.form.Close();self.form.Dispose();self.form=None
        if self in self.context.owned:self.context.owned.remove(self)
    def close(self):
        if self.closed:return
        try:self.context.manager.bridge.call(lambda r:(self._dispose(),r.finish(True)),timeout=3)
        except Exception:
            # Retain ownership so shutdown can retry disposal on the STA.
            self.closed=True
            self.load_done.set()
