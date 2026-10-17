/* UI-only translations; account identifiers, model IDs, log evidence and form values stay unchanged. */
(() => {
const words = {
'错误与帮助':'Errors & Help',
'删除账号':'Delete account','账号名称':'Account name','确认删除':'Confirm deletion','账号已删除，历史记录已保留':'Account deleted; history preserved',
'已核对账号身份并打开浏览器':'Identity verified; account browser opened','已打开独立登录窗口，请核对账号':'Dedicated login window opened; check the account',
'启停：待服务升级':'Start/stop: upgrade pending','需要先升级账号服务':'Upgrade the account service first',
'打开账号浏览器':'Open account browser','停止账号':'Stop account','启动账号':'Start account','已启用新请求':'New requests enabled','已停止新请求':'New requests stopped',
'删除':'Delete','账号名称不匹配':'Account name does not match',
'删除后将从账号目录移除，保留历史用量和浏览器文件。请输入账号名称确认：':'Remove this account from the directory while retaining usage history and browser files. Type the account name to confirm:',
'通道说明 / Channel guide':'Channel guide',
'① Cookie：旧协议兼容，仅保留旧模型。② 已有浏览器服务：连接当前8787的四账号池，可选择Gemini 4 Argon。③ 本台托管池：使用在本后台新录入并启用的账号，与原四账号池独立。':'1. Cookie: legacy protocol and legacy models. 2. Existing browser service: connects to the four-account pool on 8787, including Gemini 4 Argon. 3. Managed pool: newly enrolled accounts, separate from production.',
'API 设置 / API settings':'API settings','新 API 密钥 / New API key':'New API key','保存 API 密钥 / Save API key':'Save API key',
'浏览器与托管通道共用管理台端口8791；上游地址是8787，不是对外调用地址。Cookie通道使用上面的独立API端口。修改这里的密钥不会修改8787上游密钥或管理密码。':'Browser and managed channels share console port 8791. Port 8787 is the upstream, not this console’s client endpoint. Cookie uses its separate API port above. This key change does not change the upstream or management key.',
'请求可以显式指定 model，例如 gemini-4-argon；不传 model 时使用保存的默认模型。修改默认模型不会覆盖调用方显式传入的模型。':'Requests may specify model, such as gemini-4-argon. Omit model to use the saved default. Explicit client model values override the default.',
'API密钥已保存，请同步更新调用方。 / API key saved. Update your clients.':'API key saved. Update your clients.',
'网页账号 · 请求与用量':'Web accounts · Requests & usage','主导航':'Main navigation',
'◫　运行概览':'◫　Overview','≡　调用记录':'≡　Requests','◎　账号管理':'◎　Accounts','⚙　运行设置':'⚙　Settings',
'运行概览':'Overview','调用记录':'Requests','账号管理':'Accounts','运行设置':'Settings',
'本机管理 · 第一版':'Local management · v1','统计时区：北京时间':'Reporting timezone: Asia/Shanghai',
'不保存密码或对话正文':'No passwords or conversation text stored','看清每个账号的每一次请求':'Every request, attributed to its account',
'未连接':'Disconnected','刷新':'Refresh','管理登录':'Admin login','今天':'Today','最近5小时':'Last 5 hours','本周':'This week','本月':'This month','自选日期':'Date range',
'时间范围':'Time range','开始日期':'Start date','结束日期':'End date','查询':'Apply','按账号筛选':'Filter by account','全部账号':'All accounts','记录来源':'Source',
'去重总览':'Deduplicated overview','现有生产服务':'Production service','本台托管账号':'Managed accounts','备选入口观测（不并入总览）':'Relay observations (excluded from overview)','原 Cookie 服务':'Original Cookie service',
'每日调用':'Daily requests','按开始时间归属日期':'Grouped by request start date','账号请求分布':'Requests by account','当前筛选时段':'Selected time range',
'逐次请求明细':'Request details','上一页':'Previous','下一页':'Next',
'时间显示到秒。请求重试分别计数；“成功”表示接口返回，不代表章节质检通过。这里只记录本机接口流量，不读取网页手工聊天历史。':'Times include seconds. Retries count separately. Success means the API returned, not that chapter review passed. Manual browser chats are not tracked.',
'账号目录':'Account directory','固定内部 ID；重命名不影响历史记录':'Stable internal IDs preserve history when renamed','＋ 录入账号':'+ Add account',
'录入流程：独立浏览器窗口 → 管理者登录 → 填写邮箱核对 → 验证成功 → 启用。新账号加入本控制台的“托管浏览器池”，不会自动改动生产池。现有生产账号仅观测，可修改显示名称。':'Enrollment: isolated browser → sign in manually → confirm email → verify → enable. New accounts join this console’s managed pool. Production accounts remain observation-only; display names can be changed.',
'原 Cookie 通道、现有浏览器服务和新账号托管池分别管理。启停本台入口不会启停现有生产服务。':'Cookie, existing browser and managed account channels remain separate. Starting or stopping this console does not start or stop production.',
'通道与模型设置':'Transport and model settings','连接本机管理台':'Connect to the local console','管理密码位于 data/settings.json 的 admin_key。密码仅保留在当前页面内存。':'Find admin_key in data/settings.json. The key is kept only in this page’s memory.',
'管理密码':'Management key','取消':'Cancel','连接':'Connect','录入账号':'Add account','名称（可留空）':'Name (optional)','创建并打开登录页':'Create and open login',
'正在加载…':'Loading…','加载失败':'Loading failed','这个范围内暂无记录。可切换日期或来源查看。':'No records in this range. Try another date or source.',
'成功':'Success','失败':'Failed','执行中':'Running','中断／未知':'Interrupted / unknown','外部生产':'Production','待登录':'Awaiting login','已验证':'Verified',
'请求次数':'Requests','实际输入 / 输出':'Reported input / output','暂未提供':'Not provided','仅统计上游明确返回的 usage':'Only explicit upstream usage',
'粗估输入 / 输出':'Estimated input / output','字符数 ÷ 4；不是计费用量':'Characters ÷ 4; not billing usage','用量未知':'Unknown usage',
'日期':'Date','调用':'Requests','成功 / 失败':'Success / failed','账号':'Account','开始时间 / 耗时':'Started / duration','账号 / 模型':'Account / model','结果':'Result','输入 token':'Input tokens','输出 token':'Output tokens','计量依据':'Measurement','详情':'Details','未记录结束':'No end recorded','上游实际':'Upstream reported','字符粗估':'Character estimate','未知':'Unknown','展开':'Expand',
'名称 / 邮箱':'Name / email','归属 / 状态':'Pool / status','当前时段调用':'Requests in range','最近验证':'Last verified','操作':'Actions','重命名':'Rename','恢复':'Restore','打开登录页':'Open login','验证身份':'Verify identity','停用':'Disable','启用':'Enable','归档':'Archive','尚未核对邮箱':'Email not verified','历史账号':'Historical account','现有生产池':'Production pool','本台托管':'Managed here','已归档':'Archived','只读观测':'Observation only','已启用':'Enabled',
'录入新账号':'Add a new account','核对登录身份':'Verify signed-in identity','重命名账号':'Rename account',
'将打开独立 Chrome 档案，名称默认按北京时间日期生成；同日自动加序号。':'Opens an isolated Chrome profile. The default name uses the date in Asia/Shanghai, with a suffix for same-day accounts.',
'请先在该账号独立窗口登录 Gemini，再填写实际邮箱。网页建议使用英文界面。':'Sign in to Gemini in this account’s isolated window, then enter the signed-in email. Use the English Gemini interface.',
'仅修改显示名称，历史统计和浏览器档案保持不变。':'Changes the display name only. History and browser profile remain unchanged.',
'实际登录邮箱':'Signed-in email','名称（录入时可留空）':'Name (optional when adding)','验证并录入':'Verify account','保存名称':'Save name',
'已保存。新录入账号验证后，需手动启用才能参与本台托管池。':'Saved. New accounts must be verified and manually enabled before joining the managed pool.',
'归档会停止该托管账号的新请求，但不删除档案和历史。继续？':'Archive this account? New requests stop; its profile and history are retained.',
'限额恢复时间（北京时间）':'Quota reset (Asia/Shanghai)','冷却中':'Cooling down','允许基础模型':'Basic model allowed','已到恢复时间':'Reset time reached','默认估计12小时':'Default estimate: 12 hours','网页恢复时间':'Reset time from webpage',
'限额保护':'Quota protection','检测到限额后暂停账号（推荐）':'Pause the account when a quota limit is detected (recommended)',
'优先读取网页恢复时间；读取不到时冷却12小时。取消勾选表示管理员允许使用基础 Flash-Lite 模型继续，实际模型会如实记录。已发送的请求不自动重发。':'Use the webpage reset time when available; otherwise pause for 12 hours. Uncheck to explicitly allow the basic Flash-Lite model. The actual model is recorded. Submitted requests are never automatically resent.',
'保存限额策略':'Save quota policy','已保存限额策略':'Quota policy saved',
'适用于本台托管池和已接入的本机浏览器服务；生产服务首次升级需要重启，之后策略保存即时生效。Cookie 通道不具备网页限额检测。':'Applies to managed accounts and the integrated local browser service. Production needs one restart after this upgrade; subsequent policy changes take effect immediately. Cookie transport cannot detect webpage quota banners.',
'允许降级为基础 Flash-Lite 模型继续？返回质量可能降低。':'Allow fallback to basic Flash-Lite? Output quality may be lower.',
'运行策略 / Runtime policy':'Runtime policy','管理密码 / Management key · data/settings.json → admin_key':'Management key · data/settings.json → admin_key','连接 / Connect':'Connect','调用通道 / Transport':'Transport','原 Cookie 通道 / Original Cookie API':'Original Cookie API','备选：已有浏览器服务 / Existing browser service':'Existing browser service','本台托管账号池 / Managed browser accounts':'Managed browser accounts',
'已有服务地址 / Browser upstream':'Browser upstream URL','已有服务 API 密钥 / Upstream API key':'Upstream API key','留空保留已保存密钥 / Leave empty to retain':'Leave empty to retain saved key','读取可用模型 / Refresh models':'Refresh available models',
'仅连接现有服务，不启停它、不新开浏览器。所有轮询账号共同支持的模型才可选择；不自动降级。浏览器需要保持运行。':'Connects to the existing service without restarting it or opening browsers. Select models shared by enabled accounts. Browsers must remain open. Fallback requires explicit quota-policy opt-in.',
'严格 Pro 校验 / Strict Pro':'Strict Pro verification','开启：无法确认付费权限和动态Pro模型时拒绝。关闭：跳过额外校验，不代表保证实际模型，不自动转换成Flash。':'When enabled, reject requests without confirmed paid entitlement and a dynamic Pro model. Disabling skips that extra check, not model validation.',
'请求模型 / Requested model':'Requested model','API端口 / API port':'API port','代理 / Proxy · 留空忽略账号代理':'Proxy · leave empty to ignore account proxy','保存 / Save':'Save','启动 API':'Start API','停止 API':'Stop API','短请求验证所选模型 / Test selected model':'Test selected model','服务状态 / Service':'Service status','未连接 / Offline':'Offline','账号池 / Accounts':'Accounts',
'连接后显示账号名称。Cookie不会通过管理API返回。':'Connect to view accounts. Cookies are never returned by the management API.',
'账号文件：data/accounts/*.json':'Account files: data/accounts/*.json','API密钥：data/settings.json → api_key':'API key: data/settings.json → api_key','运行日志：data/server.log':'Runtime log: data/server.log',
'Cookie 配置需停止自管进程后保存；浏览器模型保存后从下一请求生效。入口已启用不等于上游生成成功。浏览器通道仍保留上游自身的 Pro 权限校验。':'Stop the managed Cookie process before changing its configuration. Browser model changes apply to subsequent requests. An enabled endpoint does not guarantee upstream generation. Upstream Pro checks remain in effect.',
'先读取模型列表，再选择并保存 / Refresh models before selecting':'Refresh models, then select and save','入口已启用 / Enabled':'Enabled','已停止 / Stopped':'Stopped','仅使用账号管理中已验证并启用的独立账号':'Uses only verified and enabled managed accounts','复用已有浏览器账号池；不会导入或改写 Cookie / Existing upstream pool':'Existing upstream pool; no Cookie import or overwrite','暂无账号 / No accounts':'No accounts','完成 / Done':'Done','连接中断 / Disconnected':'Disconnected','正在发送短请求，不会自动替换模型…':'Sending a short test request…',
'停用备选入口，不关闭上游，已提交请求继续完成。继续？':'Disable this endpoint? The upstream stays open and submitted requests continue.',
'停止会中断自管 API 的请求。继续？ / Stop active requests?':'Stop the managed API and interrupt active requests?'
};
const original = new WeakMap();
window.uiLanguage = localStorage.getItem('gemini-console-language') === 'en' ? 'en' : 'zh';
window.translateUI = text => {
 if(window.uiLanguage!=='en')return text;
 const trim=text.trim(); if(words[trim])return text.replace(trim,words[trim]);
 return text.replace(/^已连接 · /,'Connected · ').replace(/^(\d[\d,]*) 成功 \/ (\d[\d,]*) 失败(?: \/ (\d[\d,]*) 执行中)?$/,(_,a,b,c)=>`${a} success / ${b} failed${c===undefined?'':` / ${c} running`}`)
 .replace(/^(\d[\d,]*) 次缺少结束事件$/,'$1 missing end events').replace(/^共 (.+) 次 · 每页100条$/,'$1 requests · 100 per page').replace(/^第 (\d+) 页$/,'Page $1')
 .replace(/^(\d[\d,.]*) 秒$/,'$1 sec').replace(/^(\d[\d,]*) 次$/,'$1 requests').replace(/ · 未启用$/,' · Disabled').replace(/^已验证/,'Verified').replace(/^待登录/,'Awaiting login')
 .replace(/^来源：/,'Source: ').replace(/^结束：/,'Ended: ').replace(/^返回模型: /,'Returned model: ').replace(/\n账号: /g,'\nAccount: ').replace(/\n回复: /g,'\nResponse: ')
 .replace(/^历史日志仍在导入，当前统计尚不完整，请稍后刷新。 /,'History import is in progress; totals are incomplete. Refresh shortly. ')
 .replace(/对话次数按模型请求轮次统计.*$/,'Counts represent model request turns, not chat windows. Historical tokens are rough character ÷ 4 estimates, not Google billing usage. Failed usage is unknown. Relay observations are excluded to avoid duplicates.');
};
function translate(){
 observer.disconnect();
 document.documentElement.lang=window.uiLanguage==='en'?'en':'zh-CN';
 if(document.querySelector('aside'))document.title=window.uiLanguage==='en'?'Gemini Console · Management':'Gemini Console · 运行管理';
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let node;
 while(node=walker.nextNode()){
   if(node.parentElement.closest('script,style,textarea'))continue;
   const prior=original.get(node),src=prior&&node.nodeValue===prior.out?prior.src:node.nodeValue;
   const out=window.translateUI(src);if(node.nodeValue!==out)node.nodeValue=out;original.set(node,{src,out});
 }
 document.querySelectorAll('[aria-label],[placeholder],[title]').forEach(el=>{
   for(const attr of ['aria-label','placeholder','title']){if(!el.hasAttribute(attr))continue;const k='data-i18n-'+attr;if(!el.hasAttribute(k))el.setAttribute(k,el.getAttribute(attr));el.setAttribute(attr,window.translateUI(el.getAttribute(k)));}
 });
 observer.observe(document.body,{subtree:true,childList:true,characterData:true});
}
function setLanguage(lang){window.uiLanguage=lang;localStorage.setItem('gemini-console-language',lang);translate();window.dispatchEvent(new Event('uilanguage'));}
const observer=new MutationObserver(translate);
for(const target of [document.querySelector('header .actions'),document.querySelector('#auth .actions')].filter(Boolean)){
 const button=document.createElement('button');button.textContent='中文 / English';button.type='button';button.onclick=()=>setLanguage(window.uiLanguage==='en'?'zh':'en');target.prepend(button);
}
window.addEventListener('storage',e=>{if(e.key==='gemini-console-language'){window.uiLanguage=e.newValue;translate();window.dispatchEvent(new Event('uilanguage'))}});
translate();
})();
