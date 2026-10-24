'use strict';
const $ = id => document.getElementById(id);
const categories = [['fixed768','▦','768p 固定规格'],['gpu_pool','⇄','GPU池调度验证'],['all','◈','当前规格全部'],['generation','ϟ','生成加速'],['stage2','⇢','H3 → LTX-2.5'],['post','▧','NVIDIA 后处理'],['reference','♫','参考与音频'],['history','◷','历史测试']];
let records = [], shown = [], category = new URLSearchParams(location.search).get('category') === 'gpu_pool' ? 'gpu_pool' : 'fixed768', selected = new Set(), players = new Map();
let playing = false, master = null, suppressedUntil = 0, seeking = false;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const targets = () => [...players.entries()].filter(([id]) => selected.has(id)).map(([,v]) => v);
const suppress = () => { suppressedUntil = performance.now() + 350; };
const synced = () => $('sync').checked;
function clock(value) { $('seek').value = value; $('time').value = Number(value).toFixed(2); }
function audio() {
  for (const [id,v] of players) v.muted = id !== $('audio').value;
}
function status(message) {
  $('sync-status').textContent = message || `${targets().length} 个已选视频 · ${synced() ? '同步播放' : '独立播放'} · ${playing ? '播放中' : '已暂停'}`;
}
function pauseAll() {
  suppress(); playing = false;
  for (const v of targets()) { v.pause(); v.playbackRate = 1; }
  status();
}
function seekAll(time) {
  suppress();
  for (const v of targets()) if (v.readyState > 0) v.currentTime = Math.min(time, Math.max(0, v.duration - .01));
  clock(time);
}
async function playAll(source) {
  const group = targets();
  if (!group.length) { status('先勾选要比较的视频。'); return; }
  master = source && group.includes(source) ? source : group[0];
  const position = master.currentTime >= 4.98 ? 0 : master.currentTime;
  suppress(); playing = true;
  for (const v of group) {
    if (v.readyState > 0 && Math.abs(v.currentTime - position) > .08) v.currentTime = position;
    v.playbackRate = 1;
  }
  audio();
  const outcomes = await Promise.allSettled(group.map(v => v.play()));
  const failed = outcomes.filter(r => r.status === 'rejected').length;
  if (failed) {
    status(`${group.length - failed}/${group.length} 个视频已播放；部分视频暂未就绪，请再次点击播放全部。`);
  } else status();
}
function syncEvent(v, event) {
  if (!synced() || performance.now() < suppressedUntil || !selected.has(v.dataset.id)) return;
  if (event === 'play' && !playing) playAll(v);
  if (event === 'pause' && playing && !v.ended) pauseAll();
  if (event === 'seeking') { master = v; seekAll(v.currentTime); }
}
function listForCategory(key) { return records.filter(r => `${r.width}x${r.height}@${r.fps}` === $('spec').value && (key === 'fixed768' ? ['fixed768_v2','fixed768_v3'].includes(r.benchmark_group) && r.formal && !r.draft : key === 'all' ? true : key === 'history' ? r.benchmark_group === 'archive' : r.category === key)); }
function renderNav() {
  $('categories').innerHTML = categories.map(([key,icon,label]) => `<button data-category="${key}" class="${category === key ? 'active' : ''}"><span class="icon">${icon}</span>${label}<span class="number">${listForCategory(key).length}</span></button>`).join('');
  for (const button of $('categories').querySelectorAll('button')) button.onclick = () => { category = button.dataset.category; if (category === 'fixed768') $('spec').value = '1344x768@24'; render(); };
}
function renderAudio() {
  const choice = $('audio').value;
  $('audio').innerHTML = '<option value="none">全部静音</option>' + shown.filter(r=>selected.has(r.id)).map(r=>`<option value="${esc(r.id)}">${esc(r.title)}</option>`).join('');
  if ([...$('audio').options].some(o=>o.value===choice)) $('audio').value=choice;
  audio();
}
function render() {
  suppress();
  for (const v of players.values()) v.pause();
  playing = false; master = null; players.clear(); clock(0);
  shown = listForCategory(category).filter(r => !$('selected-only').checked || selected.has(r.id));
  if (category === 'fixed768') shown.sort((a,b)=>(b.assessment?.level==='recommended')-(a.assessment?.level==='recommended'));
  renderNav();
  $('protocol').textContent = category === 'fixed768' ? '固定对照：1344×768 · 24fps · 120帧 · 5秒；H3草稿896×512，提示词、seed与步数固定。每次清空节点缓存并卸载GPU模型；系统文件与内核缓存保留，首次编译计入耗时。先独立测试，再测试叠加。' : '历史与其他实验仅展示所选尺寸/帧率。不同提示词、seed、草稿尺寸或缓存条件的记录仅供查看，不作为严格耗时对照。';
  $('count').textContent = `${shown.length} 个视频 / 共 ${records.length} 个`;
  $('grid').innerHTML = shown.length ? shown.map(r => {
    const acceleration = r.acceleration.filter(a => !a.startsWith('LoRA:'));
    if (!acceleration.length) acceleration.push('原生 Attention / 原生 VAE');
    const cost = r.seconds == null ? '未记录' : `${r.seconds.toFixed(2)} 秒`;
    return `<article class="card ${selected.has(r.id) ? '' : 'unselected'}" data-id="${esc(r.id)}"><div class="media"><video controls playsinline preload="metadata" muted poster="${esc(r.poster_url)}" data-id="${esc(r.id)}" aria-label="${esc(r.title)}"><source src="${esc(r.media_url)}" type="video/mp4"></video><label class="choose"><input type="checkbox" ${selected.has(r.id)?'checked':''} aria-label="选择 ${esc(r.title)}">加入对比</label></div><div class="card-heading"><h2>${esc(r.title)}</h2></div><div class="assessment ${esc(r.assessment?.level || 'archive')}"><strong>${esc(r.assessment?.verdict || '待评估')}</strong><p>${esc(r.assessment?.reason || '尚无视觉评估结论')}</p></div><p class="cost">${cost}<small>${r.timing_scope==='pipeline_for_draft'?'整链路（草稿未分段）':r.timing_scope==='draft_stage'?'草稿阶段':r.category==='post'?'仅后处理':'执行耗时'}</small></p><p class="meta">${r.width} × ${r.height} · ${r.fps.toFixed(0)} fps · ${r.duration.toFixed(2)} s · seed ${esc(r.seed ?? '未记录')}</p><p class="method">${esc(acceleration.join(' · '))}</p><div class="actions"><a href="${esc(r.workflow_url)}" download="${esc(r.id)}.json">♧ 工作流</a><a href="${esc(r.media_url)}" download="${esc(r.id)}.mp4">↓ 下载视频</a><time>${new Date(r.date).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}</time></div><details class="evidence"><summary>加速配置与运行证据</summary><p>${r.notes.map(esc).join('<br>')}</p><p>端到端请求：${r.wall_seconds == null ? '未记录' : r.wall_seconds.toFixed(3)+' s'}<br>缓存节点：${esc(r.cached_nodes.join(', ') || '无')}<br>记录：${esc(r.evidence)}</p><p><strong>完整加速配置</strong><br>${r.acceleration.map(esc).join('<br>')}</p><p><strong>提示词</strong><br>${esc(r.prompt || '原记录未保存')}</p><details><summary>模型文件与各节点耗时</summary><ul>${r.models.map(m=>`<li>${esc(m)}</li>`).join('')}</ul><pre>${esc(JSON.stringify(r.node_seconds,null,2))}</pre></details></details></article>`;
  }).join('') : '<p class="empty">没有符合条件的视频。请调整分类或选择范围。</p>';
  for (const article of $('grid').querySelectorAll('.card')) {
    const id = article.dataset.id, v = article.querySelector('video'); players.set(id,v);
    article.querySelector('.choose input').onchange = event => {
      if (event.target.checked) selected.add(id); else { selected.delete(id); suppress(); v.pause(); }
      article.classList.toggle('unselected',!selected.has(id));
      renderAudio();
      if ($('selected-only').checked) render();
      else if (playing) playAll(master); else status();
    };
    v.addEventListener('volumechange',()=> {
      if (!v.muted && $('audio').value !== id) { $('audio').value=id; audio(); }
    });
    for (const event of ['play','pause','seeking']) v.addEventListener(event,()=>syncEvent(v,event));
    v.addEventListener('ended',()=> {
      if (!synced()) { if ($('loop').checked) v.play().catch(()=>{}); return; }
      if (playing && (v===master || targets().every(p=>p.ended))) {
        if ($('loop').checked) { seekAll(0); playAll(); } else { pauseAll(); clock(5); }
      }
    });
    v.addEventListener('error',()=>status('有视频加载失败，请检查本机服务与文件后刷新页面。'));
  }
  renderAudio();
  audio(); status();
}
$('refresh').onclick = () => location.reload();
$('play').onclick = () => playAll();
$('pause').onclick = pauseAll;
$('restart').onclick = () => { seekAll(0); playAll(); };
$('seek').oninput = () => { seeking = true; seekAll(Number($('seek').value)); };
$('seek').onchange = () => { seeking = false; if (playing) playAll(master); };
$('audio').onchange = audio;
$('spec').onchange = () => { if (category === 'fixed768' && $('spec').value !== '1344x768@24') category = 'all'; render(); };
$('sync').onchange = () => { if (synced() && playing) playAll(master); status(); };
$('loop').onchange = () => { for (const v of players.values()) v.loop = !synced() && $('loop').checked; };
$('select-all').onclick = () => { listForCategory(category).forEach(r=>selected.add(r.id)); render(); };
$('clear').onclick = () => { listForCategory(category).forEach(r=>selected.delete(r.id)); render(); };
$('selected-only').onchange = render;
for (const [id,three] of [['three',true],['two',false]]) $(id).onclick = () => { $('grid').classList.toggle('three',three); $('three').classList.toggle('active',three); $('two').classList.toggle('active',!three); };
setInterval(()=> {
  if (!playing || !master || seeking) return;
  const group = targets();
  if (!group.includes(master)) master = group.find(v=>!v.paused) || group[0];
  if (!master) { pauseAll(); return; }
  if (synced() && master.currentTime >= 5) {
    if ($('loop').checked) { seekAll(0); playAll(); } else { pauseAll(); clock(5); }
    return;
  }
  clock(master.currentTime);
  if (!synced()) return;
  for (const v of group) {
    if (v === master || v.readyState < 2 || v.seeking) continue;
    const drift = master.currentTime - v.currentTime;
    if (Math.abs(drift) > .25) { suppress(); v.currentTime = master.currentTime; }
    else v.playbackRate = Math.abs(drift) > .06 ? (drift > 0 ? 1.05 : .95) : 1;
  }
},120);
(async()=> {
  try {
    const response = await fetch('/oneapi/v1/video/comparison');
    if (!response.ok) throw new Error(response.status === 401 ? '请先登录 ComfyUI，再打开对比页面。' : `数据加载失败：HTTP ${response.status}`);
    const catalog = await response.json(); records = catalog.records;
    if (catalog.recommendation) {
      const r = catalog.recommendation;
      $('recommendation').hidden = false;
      $('recommendation').innerHTML = `<span class="recommendation-tag">已设为默认</span><h2>${esc(r.title)}</h2><p>${esc(r.timing)} · ${esc(r.reason)}</p><small>${esc(r.limitations)}</small><details><summary>默认配置与评估方法</summary><pre>${esc(JSON.stringify(r.settings,null,2))}</pre><p>OneAPI 工作流：${esc(r.workflow_name)}<br>提交接口：${esc(r.api)}<br>全部视频检查 0 / 2 / 4 秒抽帧，候选另检查原尺寸细节；不提供虚构画质分数。</p></details>`;
    }
    const specs = [...new Set(records.map(r=>`${r.width}x${r.height}@${r.fps}`))];
    $('spec').innerHTML = specs.map(s=>{const [size,fps]=s.split('@');return `<option value="${esc(s)}">${size.replace('x',' × ')} · ${fps} fps</option>`;}).join('');
    $('spec').value = '1344x768@24';
    if (!records.some(r=>['fixed768_v2','fixed768_v3'].includes(r.benchmark_group) && r.formal && !r.draft)) category='all';
    selected = new Set(records.map(r=>r.id));
    $('failure-title').textContent=`失败记录（${catalog.failures.length} 次，未计入成功视频）`;
    $('failures').innerHTML=catalog.failures.map(r=>`<p><strong>${esc(r.id)}</strong> · ${esc(r.reason)}</p>`).join('');
    render();
  } catch(error) { $('error').hidden=false; $('error').textContent=error.message; }
})();
