"use strict";
const $ = selector => document.querySelector(selector);
const state = {user:null, csrf:"", view:"overview", authMode:"login", cart:new Map(), requestKey:crypto.randomUUID(),
  products:[], pages:{products:1,orders:1,movements:1}, filters:{products:{},orders:{},movements:{}}};
const statuses = {pending:"待付款",paid:"待发货",shipped:"待收货",completed:"已完成",cancelled:"已取消"};
const reasons = {initial:"初始入库",manual:"人工调整",order:"下单扣减",cancel:"取消归还"};
const titles = {overview:["OVERVIEW","工作概览","订单进度与日常业务，一目了然。"],products:["PRODUCTS","商品与库存","维护商品信息，查看库存，或者挑选你的下一笔订单。"],orders:["ORDERS","订单管理","从创建到完成，查看每一步的流转。"],movements:["STOCK HISTORY","库存流水","每一次库存变动，都有记录可以追溯。"]};
const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const cash = value => Number(value).toLocaleString("zh-CN",{minimumFractionDigits:2,maximumFractionDigits:2});
const date = value => value ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : "—";
const pill = status => `<span class="pill ${esc(status)}">${esc(statuses[status]||status)}</span>`;
const isAdmin = () => state.user?.role === "admin";
let toastTimer;
function toast(message,error=false){const el=$("#toast");el.textContent=message;el.classList.toggle("error",error);el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{el.hidden=true;},4000);}
async function api(path,{method="GET",body}={}){
  const headers={};if(body!==undefined){headers["Content-Type"]="application/json";}
  if(method!=="GET"){headers["X-CSRF-Token"]=state.csrf;}
  let result;try{result=await fetch(`/api${path}`,{method,headers,credentials:"same-origin",body:body===undefined?undefined:JSON.stringify(body)});}catch{throw new Error("连接失败，请检查项目是否仍在运行。提交订单失败时可重试，无需重复点击。");}
  let payload;try{payload=await result.json();}catch{throw new Error("暂时无法读取响应，请稍后重试。");}
  if(payload.csrf_token)state.csrf=payload.csrf_token;
  if(!result.ok)throw new Error(payload.error?.message||"操作失败，请稍后重试。");
  return payload.data;
}
function busy(button,callback){return async event=>{event?.preventDefault();button.disabled=true;try{await callback(event);}catch(error){toast(error.message,true);}finally{button.disabled=false;}};}
function setAuthMode(mode){state.authMode=mode;$("#login-tab").classList.toggle("selected",mode==="login");$("#register-tab").classList.toggle("selected",mode==="register");$("#auth-title").textContent=mode==="login"?"登录你的工作台":"创建你的账号";$("#auth-submit").textContent=mode==="login"?"进入工作台 →":"创建账号并登录 →";$("#password").autocomplete=mode==="login"?"current-password":"new-password";$("#auth-error").textContent="";}
function showWorkspace(user){state.user=user;$("#auth-screen").hidden=!!user;$("#workspace").hidden=!user;if(!user)return;$("#profile-name").textContent=user.username;$("#profile-role").textContent=isAdmin()?"管理员":"普通用户";$("#avatar").textContent=user.username[0].toUpperCase();document.querySelectorAll(".admin-only").forEach(el=>{el.hidden=!isAdmin();});$("#today").textContent=new Date().toLocaleDateString("zh-CN",{year:"numeric",month:"long",day:"numeric",weekday:"long"});}
async function navigate(view){if(view==="movements"&&!isAdmin())return;state.view=view;document.querySelectorAll(".view").forEach(el=>{el.hidden=el.id!==`view-${view}`;});document.querySelectorAll(".nav-button").forEach(el=>el.classList.toggle("active",el.dataset.view===view));const [eyebrow,title,description]=titles[view];$("#page-eyebrow").textContent=eyebrow;$("#page-title").textContent=title;$("#breadcrumb-title").textContent=title;$("#page-description").textContent=description;await loadCurrent();}
function params(view){const p=new URLSearchParams({page:state.pages[view],page_size:view==="products"?9:10});Object.entries(state.filters[view]).forEach(([k,v])=>{if(v)p.set(k,v);});return p;}
function pagination(view,data){$(`#${view}-pagination`).innerHTML=data.total?`<span>共 ${data.total} 条 · 第 ${data.page} / ${data.pages} 页</span><button class="secondary" data-page="${view}" data-next="${data.page-1}" ${data.page<=1?"disabled":""}>上一页</button><button class="secondary" data-page="${view}" data-next="${data.page+1}" ${data.page>=data.pages?"disabled":""}>下一页</button>`:"";}
function empty(text){return `<div class="empty">${esc(text)}</div>`;}
function ordersTable(items){
  if(!items.length)return empty("暂时没有订单，去商品页面创建第一笔吧。");
  return `<div class="table-wrap"><table class="orders-data"><thead><tr>
    <th>订单编号 / 创建时间</th>${isAdmin()?"<th>用户</th>":""}
    <th>商品</th><th>金额</th><th>状态</th><th></th>
  </tr></thead><tbody>${items.map(o=>`<tr class="order-row">
    <td class="order-identity"><div class="order-number">${esc(o.order_no)}</div><div class="table-sub">${date(o.created_at)}</div></td>
    ${isAdmin()?`<td class="order-user">${esc(o.username)}</td>`:""}
    <td class="order-product">${esc(o.items[0]?.product_name||"")}<div class="table-sub">${o.items.length} 种商品</div></td>
    <td class="order-amount">¥ ${cash(o.total_amount)}</td>
    <td class="order-status">${pill(o.status)}</td>
    <td class="order-action"><button class="text-button" data-order="${o.id}">查看详情 →</button></td>
  </tr>`).join("")}</tbody></table></div>`;
}

function orderDistribution(data){
  const palette=["#767680","#aaaab2","#dedee3","#242429","#e8e8ed"];
  const circumference=2*Math.PI*54;
  let offset=0;
  const segments=Object.entries(statuses).map(([status],index)=>{
    const length=data.order_count?(data.by_status[status]||0)/data.order_count*circumference:0;
    const segment=length?`<circle class="chart-segment" cx="70" cy="70" r="54" fill="none" stroke="${palette[index]}" stroke-width="13" stroke-dasharray="${length} ${circumference-length}" stroke-dashoffset="${-offset}"/>`:"";
    offset+=length;return segment;
  }).join("");
  return `<div class="distribution"><div class="donut-chart">
    <svg viewBox="0 0 140 140" role="img" aria-label="订单状态占比，共 ${data.order_count} 笔订单">
      <circle cx="70" cy="70" r="54" fill="none" stroke="#f1f1f4" stroke-width="13"/>${segments}
    </svg><div class="donut-label"><strong>${data.order_count}</strong><span>订单总数</span></div>
  </div><div class="chart-legend">${Object.entries(statuses).map(([status,label],index)=>{
    const count=data.by_status[status]||0, percent=data.order_count?Math.round(count/data.order_count*100):0;
    return `<div class="legend-row"><span class="legend-marker marker-${index}"></span><span>${label}</span><b>${count}</b><small>${percent}%</small></div>`;
  }).join("")}</div></div>`;
}

async function loadOverview(){
  const data=await api("/dashboard");
  const cards=[
    ["订单总数",data.order_count,"全部可查看订单","icon-orders"],
    ["待付款订单",data.pending_count,"等待付款确认","icon-history"],
    [isAdmin()?"已付款订单金额":"我的已付款金额",`¥ ${cash(data.paid_amount)}`,"包含已付款、已发货和已完成","icon-bag"],
    [isAdmin()?"商品总数":"已完成订单",isAdmin()?data.product_count:(data.by_status.completed||0),isAdmin()?"包含上架与下架商品":"已确认收货","icon-package"]
  ];
  $("#metrics").innerHTML=cards.map(([label,value,note,icon],i)=>`<article class="metric ${i===0?"emphasis":""}"><div class="metric-top"><p class="metric-label">${label}</p><span class="metric-icon"><svg class="icon" aria-hidden="true"><use href="#${icon}"/></svg></span></div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></article>`).join("");
  $("#status-bars").innerHTML=orderDistribution(data);
  $("#attention-title").textContent=isAdmin()?"库存提醒":"下一步";
  $("#attention").innerHTML=isAdmin()?(data.low_stock.length?data.low_stock.map((p,i)=>`<div class="inventory-reminder"><span class="inventory-index">${String(i+1).padStart(2,"0")}</span><div class="inventory-name"><b>${esc(p.name)}</b><span>${esc(p.sku)} · 库存少于 10 件</span></div><div class="inventory-quantity"><strong>${p.stock}</strong><span>剩余库存</span></div></div>`).join("")+`<button class="inventory-link text-button" data-go="products">管理商品与库存 →</button>`:empty("当前上架商品库存充足。")):`<div class="attention-row"><span>挑选商品，创建订单</span><button class="text-button" data-go="products">浏览商品 →</button></div><div class="attention-row"><span>待付款订单可取消并归还库存</span></div><div class="attention-row"><span>发货后，可在订单中确认收货</span></div>`;
  const recent=await api("/orders?page_size=5");
  $("#recent-orders").innerHTML=ordersTable(recent.items);
}
async function loadProducts(){
  const data=await api(`/products?${params("products")}`);
  state.products=data.items;
  $("#products-grid").innerHTML=data.items.length?data.items.map(p=>`
    <article class="product-card">
      <div class="product-top">
        <span class="product-symbol"><svg class="icon" aria-hidden="true"><use href="#icon-package"/></svg></span>
        <span class="product-id">#${p.id} · ${esc(p.sku)}</span>
        ${!p.is_active?"<span class='pill cancelled'>已下架</span>":""}
      </div>
      <div class="product-body">
        <h3>${esc(p.name)}</h3>
        <p class="product-description">${esc(p.description||"暂无商品描述")}</p>
        <div class="product-pricing">
          <span class="price">¥ ${cash(p.price)}</span>
          <span class="stock-text ${p.stock<10?"low":""}">库存 ${p.stock} 件</span>
        </div>
        <div class="product-actions">
          ${isAdmin()?`<button class="secondary" data-edit-product="${p.id}">编辑</button><button class="secondary" data-stock="${p.id}">调整库存</button>`:""}
          <button class="primary" data-add="${p.id}" ${!p.is_active||p.stock<=0?"disabled":""}>${p.stock<=0?"暂无库存":"加入清单"}</button>
        </div>
      </div>
    </article>`).join(""):empty("没有找到商品。");
  pagination("products",data);
}
async function loadOrders(){const data=await api(`/orders?${params("orders")}`);$("#orders-table").innerHTML=ordersTable(data.items);pagination("orders",data);}
async function loadMovements(){const data=await api(`/stock-movements?${params("movements")}`);$("#movements-table").innerHTML=data.items.length?`<div class="table-wrap"><table><thead><tr><th>时间</th><th>商品</th><th>变动数量</th><th>类型</th><th>操作人</th><th>原因 / 订单编号</th></tr></thead><tbody>${data.items.map(m=>`<tr><td>${date(m.created_at)}</td><td>${esc(m.product_name)}<div class="table-sub">#${m.product_id}</div></td><td class="${m.delta>0?"positive":"negative"}">${m.delta>0?"+":""}${m.delta}</td><td>${esc(reasons[m.reason]||m.reason)}</td><td>${esc(m.operator)}</td><td>${esc(m.note)}<div class="table-sub">${m.order_id?`订单 #${m.order_id}`:"—"}</div></td></tr>`).join("")}</tbody></table></div>`:empty("没有符合条件的库存记录。");pagination("movements",data);}
function loadCurrent(){return ({overview:loadOverview,products:loadProducts,orders:loadOrders,movements:loadMovements})[state.view]();}
function openModal(title,content){$("#modal-title").textContent=title;$("#modal-content").innerHTML=content;if(!$("#modal").open)$("#modal").showModal();}
function confirmAction(message){
  return new Promise(resolve=>{
    const dialog=$("#modal"), box=document.createElement("div");
    box.className="notice";
    const text=document.createElement("p");text.textContent=message;
    const yes=document.createElement("button"),no=document.createElement("button");
    yes.className="primary";yes.textContent="确认操作";yes.type="button";
    no.className="secondary";no.textContent="返回";no.type="button";
    box.append(text,yes,no);$("#modal-content").append(box);
    const finish=value=>{box.remove();dialog.removeEventListener("close",close);resolve(value);};
    const close=()=>finish(false);
    yes.addEventListener("click",()=>finish(true),{once:true});
    no.addEventListener("click",()=>finish(false),{once:true});
    dialog.addEventListener("close",close,{once:true});
    yes.focus();
  });
}
function updateCart(){let count=0;for(const item of state.cart.values())count+=item.quantity;$("#cart-count").textContent=count;}
function addToCart(id){const product=state.products.find(p=>p.id===id);if(!product)return;const current=state.cart.get(id);if((current?.quantity||0)>=Math.min(product.stock,100000)){toast("清单数量已达到当前库存或单项数量上限",true);return;}state.cart.set(id,{...product,quantity:(current?.quantity||0)+1});state.requestKey=crypto.randomUUID();updateCart();toast("已加入购物清单");}
function openCart(){openModal("购物清单",`<div id="cart-items">${state.cart.size?[...state.cart.values()].map(p=>`<div class="cart-row"><div><b>${esc(p.name)}</b><div class="table-sub">¥ ${cash(p.price)} / 件</div></div><input data-cart-quantity="${p.id}" type="number" min="1" max="100000" value="${p.quantity}" aria-label="${esc(p.name)}数量"><button data-remove-cart="${p.id}" class="text-button">移除</button></div>`).join(""):empty("清单还是空的，先去挑选商品吧。")}</div>${state.cart.size?`<div class="cart-total"><span>预计金额</span><span>¥ ${cash([...state.cart.values()].reduce((sum,p)=>sum+Number(p.price)*p.quantity,0))}</span></div><p class="muted">最终金额以提交时的商品价格为准。</p><label>订单备注<textarea id="order-note" maxlength="200" rows="2" placeholder="可选，最多 200 字"></textarea></label><p class="notice">提交订单后会扣减库存。待付款时可以取消，库存会自动归还。付款仅为流程模拟。</p><button id="submit-order" class="primary full">确认创建订单 →</button>`:""}`);if(state.cart.size){$("#order-note").addEventListener("input",()=>{state.requestKey=crypto.randomUUID();});$("#submit-order").addEventListener("click",busy($("#submit-order"),async()=>{const items=[...state.cart.values()].map(p=>({product_id:p.id,quantity:p.quantity}));const result=await api("/orders",{method:"POST",body:{items,note:$("#order-note").value,request_key:state.requestKey}});state.cart.clear();state.requestKey=crypto.randomUUID();updateCart();$("#modal").close();toast("订单已创建，库存已扣减");state.pages.orders=1;await navigate("orders");await openOrder(result.id);}));}}
async function openOrder(id){const o=await api(`/orders/${id}`);const actions=[];if(o.status==="pending"){actions.push(`<button class="secondary" data-order-action="cancel" data-id="${id}">取消订单</button>`,`<button class="primary" data-order-action="pay" data-id="${id}">模拟付款</button>`);}if(o.status==="paid"&&isAdmin())actions.push(`<button class="primary" data-order-action="ship" data-id="${id}">确认发货</button>`);if(o.status==="shipped")actions.push(`<button class="primary" data-order-action="complete" data-id="${id}">确认收货</button>`);openModal("订单详情",`<div class="detail-summary"><b>${esc(o.order_no)}</b> ${pill(o.status)}<br>创建时间：${date(o.created_at)}<br>下单用户：${esc(o.username)}${o.paid_at?`<br>模拟付款时间：${date(o.paid_at)}`:""}${o.cancelled_at?`<br>取消时间：${date(o.cancelled_at)}`:""}${o.note?`<br>订单备注：${esc(o.note)}`:""}</div><div class="detail-items">${o.items.map(item=>`<div class="cart-row"><div><b>${esc(item.product_name)}</b><br><small>${esc(item.product_sku)} · ¥ ${cash(item.unit_price)} × ${item.quantity}</small></div><span>¥ ${cash(item.subtotal)}</span></div>`).join("")}</div><div class="cart-total"><span>订单总额</span><span>¥ ${cash(o.total_amount)}</span></div>${o.status==="pending"?"<p class='notice'>模拟付款只改变订单状态，不会产生真实扣款。已付款订单不支持直接取消。</p>":""}<div class="modal-footer">${actions.join("")}</div>`);}
function productForm(product=null){openModal(product?"编辑商品":"新建商品",`<form id="product-form" class="modal-form"><div class="form-grid"><label>商品名称<input name="name" maxlength="80" required value="${esc(product?.name)}"></label><label>SKU<input name="sku" maxlength="40" required value="${esc(product?.sku)}" ${product?"disabled":""}></label><label>单价（元）<input name="price" type="number" min="0.01" max="1000000" step="0.01" required value="${esc(product?.price||"")}"></label>${product?`<label>上架状态<select name="is_active"><option value="1" ${product.is_active?"selected":""}>已上架</option><option value="0" ${!product.is_active?"selected":""}>已下架</option></select></label>`:`<label>初始库存<input name="stock" type="number" min="0" max="1000000" step="1" value="0" required></label>`}</div><label>商品描述<textarea name="description" maxlength="500" rows="3">${esc(product?.description)}</textarea></label><div class="modal-footer"><button class="primary" type="submit">保存商品</button></div></form>`);const form=$("#product-form");form.addEventListener("submit",busy(form.querySelector("button[type=submit]"),async()=>{const values=Object.fromEntries(new FormData(form));const body={name:values.name,description:values.description,price:values.price};if(product)body.is_active=values.is_active==="1";else{body.sku=values.sku;body.stock=Number(values.stock);}await api(product?`/products/${product.id}`:"/products",{method:product?"PATCH":"POST",body});$("#modal").close();toast("商品已保存");await loadProducts();}));}
function stockForm(product){openModal("调整库存",`<p class="muted">${esc(product.name)} · 当前库存 ${product.stock} 件</p><form id="stock-form" class="modal-form"><label>增减数量<input name="delta" type="number" min="-1000000" max="1000000" step="1" required placeholder="正数入库，负数出库"></label><label>调整原因<textarea name="note" maxlength="200" required rows="2" placeholder="例如：采购补货、盘点修正"></textarea></label><div class="modal-footer"><button class="primary" type="submit">确认调整</button></div></form>`);const form=$("#stock-form");form.addEventListener("submit",busy(form.querySelector("button"),async()=>{const values=Object.fromEntries(new FormData(form));await api(`/products/${product.id}/stock`,{method:"POST",body:{delta:Number(values.delta),note:values.note}});$("#modal").close();toast("库存已调整，流水已记录");await loadProducts();}));}

$("#login-tab").addEventListener("click",()=>setAuthMode("login"));$("#register-tab").addEventListener("click",()=>setAuthMode("register"));
$("#auth-form").addEventListener("submit",async event=>{event.preventDefault();const button=$("#auth-submit");button.disabled=true;$("#auth-error").textContent="";try{const data=await api(`/auth/${state.authMode}`,{method:"POST",body:{username:$("#username").value,password:$("#password").value}});$("#password").value="";showWorkspace(data.user);await navigate("overview");}catch(error){$("#auth-error").textContent=error.message;}finally{button.disabled=false;}});
$("#logout").addEventListener("click",busy($("#logout"),async()=>{await api("/auth/logout",{method:"POST",body:{}});state.cart.clear();updateCart();$("#modal").close();showWorkspace(null);setAuthMode("login");}));
$("#refresh").addEventListener("click",busy($("#refresh"),loadCurrent));$("#close-modal").addEventListener("click",()=>$("#modal").close());$("#open-cart").addEventListener("click",openCart);$("#new-product").addEventListener("click",()=>productForm());
for(const view of ["products","orders","movements"]){const form=$(`#${{products:"product",orders:"order",movements:"movement"}[view]}-search`);form.addEventListener("submit",busy(form.querySelector("button"),async()=>{state.filters[view]=Object.fromEntries(new FormData(form));state.pages[view]=1;await loadCurrent();}));}
document.addEventListener("click",async event=>{const button=event.target.closest("button");if(!button||button.disabled)return;try{
  if(button.dataset.view)await navigate(button.dataset.view);
  if(button.dataset.go)await navigate(button.dataset.go);
  if(button.dataset.page){state.pages[button.dataset.page]=Number(button.dataset.next);await loadCurrent();}
  if(button.dataset.add)addToCart(Number(button.dataset.add));
  if(button.dataset.order)await openOrder(Number(button.dataset.order));
  if(button.dataset.editProduct)productForm(state.products.find(p=>p.id===Number(button.dataset.editProduct)));
  if(button.dataset.stock)stockForm(state.products.find(p=>p.id===Number(button.dataset.stock)));
  if(button.dataset.removeCart){state.cart.delete(Number(button.dataset.removeCart));state.requestKey=crypto.randomUUID();updateCart();openCart();}
  if(button.dataset.orderAction){const action=button.dataset.orderAction,id=Number(button.dataset.id);const messages={cancel:"确认取消订单并归还库存？",pay:"确认模拟付款？这不会产生真实扣款。",ship:"确认该订单已经发货？",complete:"确认已经收到商品？"};button.disabled=true;try{if(!await confirmAction(messages[action]))return;await api(`/orders/${id}/${action}`,{method:"POST",body:{}});toast("订单状态已更新");await openOrder(id);await loadCurrent();}finally{button.disabled=false;}}
}catch(error){toast(error.message,true);}});
document.addEventListener("change",event=>{if(!event.target.dataset.cartQuantity)return;const id=Number(event.target.dataset.cartQuantity),quantity=Number(event.target.value);if(!Number.isInteger(quantity)||quantity<1||quantity>100000){toast("数量需为 1 至 100000 的整数",true);openCart();return;}state.cart.get(id).quantity=quantity;state.requestKey=crypto.randomUUID();updateCart();openCart();});
(async()=>{try{const data=await api("/auth/session");showWorkspace(data.user);if(data.user)await navigate("overview");}catch(error){$("#auth-error").textContent=error.message;}})();
