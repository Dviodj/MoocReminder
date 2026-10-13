'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('t') || '';
const $ = s => document.querySelector(s);
let state = null, currentView = 'overview', kind = 'all', showPast = false, settingsReady = false, toastTimer;
const seenNotices = new Set();
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;

async function api(path, data) {
  const response = await fetch('/api/' + path, {method:data === undefined ? 'GET':'POST', headers:{'Authorization':'Bearer ' + token, 'Content-Type':'application/json'}, body:data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败，请重新打开程序。');
  return result;
}
function toast(message) { const node=$('#toast'); node.textContent=message; node.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>node.classList.remove('show'),3600); }
async function action(name, data={}) { try { await api(name,data); await update(); } catch(error) { toast(error.message); } }

function render(data) {
  state=data;
  $('#stat-pending').innerHTML=esc(data.summary.pending)+'<span>项</span>';
  $('#stat-soon').innerHTML=esc(data.summary.soon)+'<span>项</span>';
  $('#stat-courses').innerHTML=esc(data.summary.courses)+'<span>门</span>';
  $('#nav-count').textContent=data.summary.pending;
  $('#connection-label').textContent=data.connected?'已连接慕课':data.job==='waiting_login'?'等待完成登录':'尚未连接慕课';
  $('#connection-dot').classList.toggle('connected',data.connected);
  $('#footer-dot').classList.toggle('connected',data.connected);
  $('#login').disabled=data.busy; $('#scan').disabled=data.busy; $('#empty-login').disabled=data.busy;
  $('#scan-icon').classList.toggle('spin',data.busy && data.job==='scan');
  $('#scan-label').textContent=data.busy && data.job==='scan'?'检查中…':'检查作业';
  $('#pause').title=data.paused?'恢复自动检查':'暂停自动检查';
  $('#pause').setAttribute('aria-label',$('#pause').title);
  $('#schedule-label').textContent=data.paused?'自动检查已暂停':`每 ${data.settings.interval_minutes} 分钟检查`;
  $('#footer-status').textContent=data.busy?`${data.phase} · ${data.elapsed} 秒`:data.last_scan?`上次检查 ${new Date(data.last_scan).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'})} · ${data.phase}`:data.phase;
  const banner=$('#status-banner');
  const showBanner=data.busy || ['waiting_login','error'].includes(data.job);
  banner.classList.toggle('hidden',!showBanner);
  banner.classList.toggle('error',data.job==='error');
  $('#banner-title').textContent=data.busy?data.phase:data.job==='waiting_login'?'登录网页已为你打开':'需要重新连接';
  $('#banner-description').textContent=data.busy?`已等待 ${data.elapsed} 秒。界面可以继续操作，也可以取消当前连接。`:data.job==='waiting_login'?'请在慕课官网完成登录，然后点击右侧“我已登录”读取作业。':data.phase;
  $('#cancel').classList.toggle('hidden',!data.busy);
  $('#login-done').classList.toggle('hidden',data.job!=='waiting_login' || data.busy);
  renderTasks(); renderCourses();
  $('#detail-message').textContent=data.phase;
  $('#detail-errors').textContent=data.errors.length?data.errors.join('\n\n'):'暂无错误记录。';
  $('#data-location').textContent='本地数据位置：'+data.data_folder;
  if (currentView==='settings' && !settingsReady) hydrateSettings();
  for (const notice of data.notifications) {
    if (!seenNotices.has(notice.id)) { seenNotices.add(notice.id); showNotice(notice); }
  }
}
function renderTasks() {
  if(!state) return;
  const query=$('#search').value.trim().toLocaleLowerCase();
  const visible=state.items.filter(item=>(showPast || !item.expired) && (kind==='all' || item.kind===kind) && (!query || (item.course+' '+item.name).toLocaleLowerCase().includes(query)));
  const hasItems=visible.length>0;
  $('#table-wrap').classList.toggle('hidden',!hasItems);
  $('#empty').classList.toggle('hidden',hasItems);
  $('#tasks').innerHTML=visible.map(item=>{
    const pill=item.expired?'past':item.remaining_hours!==null && item.remaining_hours<=24?'urgent':item.remaining_hours!==null && item.remaining_hours<=72?'soon':'';
    return `<tr class="${item.done?'completed-row':''}"><td><button class="task-check ${item.done?'done':''}" data-complete="${esc(item.uid)}" data-done="${item.local_completed}" aria-label="${item.local_completed?'恢复提醒':'标记已完成'}">${item.done?icon('check'):''}</button></td><td><div class="task-name">${esc(item.name)}<span class="task-kind">${esc(item.kind)}</span></div><div class="task-course">${esc(item.course)}</div></td><td class="task-deadline">${esc(item.deadline_label)}</td><td><span class="time-pill ${pill}">${esc(item.remaining)}</span></td><td class="task-status">${esc(item.status_label)}</td><td><a class="open-task" href="${esc(item.course_url)}" target="_blank" rel="noopener" title="在官网查看">${icon('arrow')}</a></td></tr>`;
  }).join('');
  const initial=!state.items.length && !state.connected;
  $('#empty-title').textContent=initial?'先连接慕课，作业就有了归处':query?'没有找到匹配的作业':showPast?'没有已发布的作业':'眼下没有待截止的作业';
  $('#empty-description').innerHTML=initial?'在官网完成登录，再回来检查一次作业。<br>截止时间与提醒会为你自动整理好。':query?'换一个关键词，或切换任务类型再试试。':'新作业发布后，下一次检查会自动更新到这里。';
  $('#empty-login').classList.toggle('hidden',!initial);
}
function renderCourses() {
  if(!state) return;
  $('#course-grid').innerHTML=state.courses.length?state.courses.map(course=>{
    const count=state.items.filter(item=>item.course_url===course.url).length;
    return `<article class="course-card"><span class="course-badge">${course.url.includes('/spoc/')?'SPOC':'MOOC'}</span><h3>${esc(course.name)}</h3><p>已读取 ${count} 项作业与测验</p><a href="${esc(course.url)}" target="_blank" rel="noopener">查看课程作业 ${icon('arrow')}</a></article>`;
  }).join(''):'<article class="course-card"><span class="course-badge">YOUR COURSES</span><h3>课程会在首次检查后显示</h3><p>连接慕课后，程序会自动读取课程主页中的 MOOC 与校内 SPOC。</p></article>';
}
function switchView(view) {
  currentView=view;
  document.querySelectorAll('[data-view]').forEach(button=>button.classList.toggle('selected',button.dataset.view===view));
  $('#overview-content').classList.toggle('hidden',!['overview','tasks'].includes(view));
  $('#courses-content').classList.toggle('hidden',view!=='courses');
  $('#settings-content').classList.toggle('hidden',view!=='settings');
  const labels={overview:['总览','作业概览','截止时间看清楚，学习节奏更从容。'],tasks:['作业清单','每一项，都有安排','核对截止时间，也为已经完成的作业画个勾。'],courses:['我的课程','学习在进行','把 MOOC 与校内课程放在同一个学习空间。'],settings:['提醒设置','按你的节奏来','检查频率与提醒时间，都可以随时调整。']};
  $('#breadcrumb-current').textContent=labels[view][0]; $('#page-title').textContent=labels[view][1]; $('#page-subtitle').textContent=labels[view][2];
  if(view==='settings') {settingsReady=false;hydrateSettings();}
}
function hydrateSettings(){
  if(!state) return;
  $('#setting-home').value=state.settings.home_url;
  $('#setting-hours').value=state.settings.reminder_hours.join(', ');
  $('#setting-interval').value=state.settings.interval_minutes;
  $('#setting-quizzes').checked=state.settings.include_quizzes;
  $('#setting-finished').checked=state.settings.include_finished;
  $('#setting-manual').value=state.settings.manual_urls.join('\n');
  settingsReady=true;
}
function showNotice(notice){
  $('#notice-title').textContent=notice.test?'提醒已准备好':'作业即将截止';
  $('#notice-description').textContent=notice.test?'这是一次测试提醒。保持程序运行，最小化后仍可提醒。':'留出一点时间，别错过这次提交。';
  $('#notice-items').innerHTML=notice.items.map(item=>`<div class="notice-item"><strong>${esc(item.course)} · ${esc(item.name)}</strong><p>提交截止：${esc(item.deadline)}<br>剩余：${esc(item.remaining)}</p></div>`).join('');
  if(!$('#notice-dialog').open) $('#notice-dialog').showModal();
}
async function update(){
  try {render(await api('state'));}
  catch(error){$('#footer-status').textContent=error.message==='未授权'?'启动链接已失效，请从启动入口重新打开。':'暂时无法连接程序，请重新打开启动入口。';}
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.view)));
$('#login').addEventListener('click',()=>action('login'));
$('#empty-login').addEventListener('click',()=>action('login'));
$('#scan').addEventListener('click',()=>action('scan'));
$('#login-done').addEventListener('click',()=>action('scan'));
$('#cancel').addEventListener('click',()=>action('cancel'));
$('#pause').addEventListener('click',async()=>{await action('pause',{paused:!state.paused});toast(state.paused?'自动检查与提醒已暂停':'自动检查与提醒已恢复');});
$('#search').addEventListener('input',renderTasks);
$('#past').addEventListener('click',()=>{showPast=!showPast;$('#past').classList.toggle('active',showPast);renderTasks();});
document.querySelectorAll('[data-kind]').forEach(button=>button.addEventListener('click',()=>{kind=button.dataset.kind;document.querySelectorAll('[data-kind]').forEach(node=>node.classList.toggle('active',node===button));renderTasks();}));
$('#tasks').addEventListener('click',async event=>{const button=event.target.closest('[data-complete]');if(!button)return;await action('complete',{uid:button.dataset.complete,completed:button.dataset.done!=='true'});toast(button.dataset.done==='true'?'已恢复这项作业的提醒':'已标记完成，停止这项作业的本地提醒');});
$('#diagnostics').addEventListener('click',()=>$('#detail-dialog').showModal());
$('.close-dialog').addEventListener('click',()=>$('#detail-dialog').close());
$('.close-detail').addEventListener('click',()=>$('#detail-dialog').close());
$('.close-notice').addEventListener('click',()=>$('#notice-dialog').close());
$('#test-notification').addEventListener('click',()=>action('test-notification'));
$('#collapse').addEventListener('click',()=>$('#workspace').classList.toggle('collapsed'));
$('#fullscreen').addEventListener('click',()=>document.fullscreenElement?document.exitFullscreen():$('#workspace').requestFullscreen());
$('#quit').addEventListener('click',async()=>{await action('quit');toast('程序已退出，自动提醒已停止。');setTimeout(()=>window.close(),350);});
$('#settings-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const payload={home_url:$('#setting-home').value.trim(),interval_minutes:Number($('#setting-interval').value),reminder_hours:$('#setting-hours').value.replaceAll('，',',').split(',').map(value=>Number(value.trim())),include_quizzes:$('#setting-quizzes').checked,include_finished:$('#setting-finished').checked,manual_urls:$('#setting-manual').value.split('\n').map(value=>value.trim()).filter(Boolean)};
  try {await api('settings',payload);$('#settings-result').textContent='已保存';toast('设置已保存');await update();}catch(error){toast(error.message);}
});
$('#export').addEventListener('click',()=>{
  if(!state)return;
  const cell=value=>'"'+String(value??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"';
  const rows=[['课程','作业名称','类型','提交截止（北京时间）','状态','官网链接'],...state.items.map(item=>[item.course,item.name,item.kind,item.deadline,item.status_label,item.course_url])];
  const blob=new Blob(['\ufeff'+rows.map(row=>row.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='作业截止时间.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('#today').textContent=new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long',timeZone:'Asia/Shanghai'}).format(new Date());
window.addEventListener('beforeunload',()=>navigator.sendBeacon('/api/closing',JSON.stringify({token})));
update();setInterval(update,1200);
