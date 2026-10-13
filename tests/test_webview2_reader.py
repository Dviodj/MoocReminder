import json
import threading
import unittest
from webview2_reader import Bridge, Request, decode_script_result, navigation_error

class Boundary:
    def __init__(self): self.pending=[]
    def post(self, fn): self.pending.append(fn)

class AdapterTests(unittest.TestCase):
    def test_timeout_ignores_late_completion(self):
        native=Boundary(); bridge=Bridge(native.post)
        with self.assertRaises(TimeoutError): bridge.call(lambda r:r.finish(9),timeout=.01)
        native.pending[0]()
        self.assertEqual(len(native.pending),1)
    def test_cancel_before_dispatch(self):
        native=Boundary(); bridge=Bridge(native.post)
        with self.assertRaises(InterruptedError): bridge.call(lambda r:r.finish(9),cancelled=lambda:True)
        self.assertEqual(native.pending,[])
    def test_cancel_in_flight(self):
        cancel=threading.Event(); native=Boundary(); bridge=Bridge(native.post)
        timer=threading.Timer(.01,cancel.set);timer.start()
        with self.assertRaises(InterruptedError): bridge.call(lambda r:None,timeout=1,cancelled=cancel.is_set)
        timer.join();native.pending[0]()
    def test_request_first_completion_wins(self):
        r=Request();r.finish(1);r.finish(2);self.assertEqual(r.result,1)
        r=Request();r.abandon();r.finish(3);self.assertIsNone(r.result)
    def test_script_result(self):
        self.assertEqual(decode_script_result(json.dumps({'ok':True,'value':[1]})),[1])
        with self.assertRaises(RuntimeError): decode_script_result('{"ok":false}')
        with self.assertRaises(RuntimeError): decode_script_result('null')
        with self.assertRaises(RuntimeError): decode_script_result('invalid')
    def test_navigation_failure(self):
        self.assertIsNone(navigation_error(True,'Unknown'))
        self.assertIsInstance(navigation_error(False,'ConnectionAborted'),RuntimeError)



class TaskBoundaryTests(unittest.TestCase):
    def test_faulted_and_late_native_script_tasks(self):
        from types import SimpleNamespace
        from webview2_reader import Page
        callbacks=[]
        class Generic:
            def __getitem__(self,item):return self
            def __call__(self,fn):return fn
        generic=Generic()
        class Task:
            def ContinueWith(self,callback,scheduler):callbacks.append(callback)
        core=SimpleNamespace(ExecuteScriptAsync=lambda script:Task())
        native=Boundary();bridge=Bridge(native.post)
        manager=SimpleNamespace(bridge=bridge,api={'Action':generic,'Task':generic,'String':str,'TaskScheduler':SimpleNamespace(FromCurrentSynchronizationContext=lambda:object())})
        context=SimpleNamespace(manager=manager,timeout=20,cancelled=lambda:False)
        page=Page(context);page.view=SimpleNamespace(CoreWebView2=core)
        outcome=[]
        thread=threading.Thread(target=lambda:self._evaluate(page,outcome));thread.start()
        while not native.pending:threading.Event().wait(.001)
        native.pending.pop(0)();thread.join(1)
        self.assertIsInstance(outcome[0],TimeoutError)
        callbacks[0](SimpleNamespace(IsFaulted=False,IsCanceled=False,Result='{"ok":true,"value":9}'))
        self.assertEqual(len(outcome),1)
        context.timeout=1000;outcome.clear();thread=threading.Thread(target=lambda:self._evaluate(page,outcome));thread.start()
        while not native.pending:threading.Event().wait(.001)
        native.pending.pop(0)();callbacks[1](SimpleNamespace(IsFaulted=True,IsCanceled=False));thread.join(1)
        self.assertIsInstance(outcome[0],RuntimeError)
    @staticmethod
    def _evaluate(page,outcome):
        try:outcome.append(page.evaluate_expression('1'))
        except Exception as exc:outcome.append(exc)
    def test_context_close_owns_only_its_pages(self):
        from webview2_reader import Context
        from pathlib import Path
        first=Context(None,Path('profile'),False,lambda:False);other=Context(None,Path('profile'),True,lambda:False)
        class Owned:
            closed=False
            def close(self):self.closed=True
        a=Owned();b=Owned();first.owned=[a];other.owned=[b]
        first.close();self.assertTrue(a.closed);self.assertFalse(b.closed);self.assertEqual(first.profile,other.profile)

class NavigationBoundaryTests(unittest.TestCase):
    def test_old_navigation_completion_cannot_finish_new_load(self):
        from types import SimpleNamespace
        from webview2_reader import Page
        errors=[];context=SimpleNamespace(visible=True,cancelled=lambda:False,manager=SimpleNamespace(on_error=errors.append))
        p=Page(context);p._starting(None,SimpleNamespace(NavigationId=2))
        p._loaded(None,SimpleNamespace(NavigationId=1,IsSuccess=True,WebErrorStatus='Unknown'))
        self.assertFalse(p.load_done.is_set())
        p._loaded(None,SimpleNamespace(NavigationId=2,IsSuccess=False,WebErrorStatus='ConnectionAborted'))
        self.assertTrue(p.load_done.is_set());self.assertIsInstance(p.failure,RuntimeError);self.assertEqual(len(errors),1)
    def test_renderer_failure_wakes_waiters(self):
        from types import SimpleNamespace
        from webview2_reader import Page
        errors=[];p=Page(SimpleNamespace(cancelled=lambda:False,manager=SimpleNamespace(on_error=errors.append)))
        p._process_failed(None,SimpleNamespace(ProcessFailedKind='RenderProcessExited'))
        self.assertTrue(p.load_done.is_set());self.assertFalse(p.load_ok);self.assertEqual(len(errors),1)




class ReconnectTests(unittest.TestCase):
    def test_visible_reconnect_disposes_prior_visible_across_accounts_only(self):
        import tempfile
        from types import SimpleNamespace
        from webview2_reader import Manager,Context
        with tempfile.TemporaryDirectory() as folder:
            m=Manager(folder);m._start=lambda:None;m.bridge=Bridge(lambda fn:fn())
            old=Context(m,m.folder/'webview2-profiles'/'old',True,lambda:False)
            hidden=Context(m,m.folder/'webview2-profiles'/'old',False,lambda:False)
            class NativePage:
                def __init__(self,c):self.context=c;self.closed=False
                def _dispose(self):self.closed=True;self.context.owned.remove(self)
            a=NativePage(old);b=NativePage(hidden);old.owned=[a];hidden.owned=[b];m._contexts=[old,hidden]
            fresh=m.context('new',True,lambda:False)
            self.assertTrue(a.closed);self.assertFalse(b.closed);self.assertIn(hidden,m._contexts);self.assertNotIn(old,m._contexts)
            scan=m.context('new',False,lambda:False)
            fresh.owned=[NativePage(fresh)];m._contexts.append(fresh)
            m.context('new',False,lambda:False)
            self.assertFalse(fresh.owned[0].closed)
    def test_disposed_page_suppresses_navigation_and_process_callbacks(self):
        from types import SimpleNamespace
        from webview2_reader import Page
        errors=[];p=Page(SimpleNamespace(visible=True,cancelled=lambda:False,manager=SimpleNamespace(on_error=errors.append)))
        p.closed=True;p._navigation_id=1
        p._loaded(None,SimpleNamespace(NavigationId=1,IsSuccess=False,WebErrorStatus='ConnectionAborted'))
        p._process_failed(None,SimpleNamespace(ProcessFailedKind='RenderProcessExited'))
        p._starting(None,SimpleNamespace(NavigationId=2))
        self.assertEqual(errors,[]);self.assertEqual(p._navigation_id,1)

if __name__=='__main__':unittest.main()
