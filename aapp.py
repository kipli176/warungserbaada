# app.py
from flask import Flask, request, jsonify, redirect, make_response

app = Flask(__name__)

# ====== Demo data (sementara) ======
DEMO_BUYERS = [
    {"id": "b-umum", "name": "Umum / Tanpa Nama", "phone_e164": None},
    {"id": "b-ani",  "name": "Ani",  "phone_e164": "+628111111111"},
    {"id": "b-budi", "name": "Budi", "phone_e164": "+628122222222"},
    {"id": "b-cici", "name": "Cici", "phone_e164": "+628133333333"},
]
# ====== Demo data investors & sales (sementara; ganti DB nanti) ======
DEMO_INVESTORS = [
    {"id": "i-2025-ani",  "name": "Ani",  "year": 2025, "amount_idr": 5_000_000, "note": ""},
    {"id": "i-2025-budi", "name": "Budi", "year": 2025, "amount_idr": 7_500_000, "note": ""},
    {"id": "i-2024-cici", "name": "Cici", "year": 2024, "amount_idr": 3_000_000, "note": ""},
]

SALES_MEM = []  # list dict: {id, sale_date, buyer, items[...], total_amount, total_cost, total_profit, paid_amount, change}
_id_seq = {"investor": 0, "sale": 0}

def _gen_id(prefix):
    _id_seq[prefix] += 1
    return f"{prefix}-{_id_seq[prefix]}"

# ====== Routes dasar ======
@app.route("/")
def home():
    return redirect("/login")

@app.route("/login")
def login_page():
    # HTML login + auth frontend-only (user statis: admin / 123456)
    html = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Login - Kelontong</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
  // ====== AUTH (FE-only, user statis) ======
  const SESS_KEY = 'kelontong_sess_v1';
  const STATIC_USER = { u: 'admin', p: '123456' }; // ganti password di sini kalau perlu

  function getSession(){ try{ return JSON.parse(localStorage.getItem(SESS_KEY)) || null }catch{return null} }
  function setSession(sess){ localStorage.setItem(SESS_KEY, JSON.stringify(sess)); }

  (function autoRedirect(){
    const sess = getSession();
    if (sess?.user) { window.location.href = '/penjualan'; }
  })();
  </script>
</head>
<body class="min-h-screen bg-gray-50 flex items-center justify-center">
  <main class="w-full max-w-sm bg-white p-6 rounded-lg shadow">
    <div class="flex items-center gap-2 mb-4">
      <h1 class="text-lg font-semibold">Masuk</h1>
    </div>

    <form id="f" class="space-y-3">
      <div>
        <label class="block text-sm mb-1">Username</label>
        <input id="u" type="text" autocomplete="username" class="w-full px-3 py-2 border rounded" required />
      </div>
      <div>
        <label class="block text-sm mb-1">Password</label>
        <input id="p" type="password" autocomplete="current-password" class="w-full px-3 py-2 border rounded" required />
      </div>
      <button class="w-full py-2 rounded bg-blue-600 text-white hover:bg-blue-700">Masuk</button>
    </form>

    <p id="err" class="text-sm text-rose-600 mt-3 hidden"></p>

    <p class="text-xs text-gray-500 mt-6">
      Gunakan akun internal. (Demo: <b>admin / 123456</b>)
    </p>
  </main>

  <script>
    const form = document.getElementById('f');
    const u = document.getElementById('u');
    const p = document.getElementById('p');
    const err = document.getElementById('err');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      err.classList.add('hidden');
      const user = u.value.trim();
      const pass = p.value;
      if (user === STATIC_USER.u && pass === STATIC_USER.p) {
        const token = Math.random().toString(36).slice(2) + Date.now().toString(36);
        setSession({ user, token, at: new Date().toISOString() });
        window.location.href = '/penjualan';
      } else {
        err.textContent = 'Username / password salah';
        err.classList.remove('hidden');
        p.value = '';
        p.focus();
      }
    });
  </script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

@app.route("/penjualan")
def penjualan_page():
    # Halaman penjualan mobile-first, pilih pembeli dulu, multi-item, total, modal bayar, simpan localStorage
    html = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Penjualan</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    dialog::backdrop { background: rgba(0,0,0,.4); }
  </style>
  <script>
  // ===== Session guard (FE-only) =====
  const SESS_KEY = 'kelontong_sess_v1';
  function getSession(){ try{ return JSON.parse(localStorage.getItem(SESS_KEY)) || null }catch{return null} }
  function logout(){ localStorage.removeItem(SESS_KEY); location.href='/login'; }
  (function guard(){
    const sess = getSession();
    if (!sess?.user) location.href = '/login';
  })();

  // ===== Utils =====
  const fmtIDR = new Intl.NumberFormat('id-ID', { style:'currency', currency:'IDR', maximumFractionDigits:0 });
  const CART_KEY = 'kelontong_cart_v1';
  const ORDER_KEY = 'kelontong_last_order_v1';

  function loadCart(){ try{ return JSON.parse(localStorage.getItem(CART_KEY)) || {buyer:null, items:[]} } catch {return {buyer:null, items:[]}} }
  function saveCart(c){ localStorage.setItem(CART_KEY, JSON.stringify(c)); }

  function escapeHtml(s=''){ return s.replace(/[&<>\"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',\"'\":'&#39;'}[c])); }

  async function fetchBuyers(){
    const r = await fetch('/api/buyers'); return r.json();
  }

  function calcTotals(items){
    let total = 0, modal = 0, laba = 0;
    for (const it of items){
      const lineTotal = it.jual * it.qty;
      const lineCost  = it.beli * it.qty;
      total += lineTotal; modal += lineCost; laba += (lineTotal - lineCost);
    }
    return { total, modal, laba };
  }

  // ===== App State =====
  let cart = loadCart();
  let buyers = [];

  async function init(){
    // load buyers
    buyers = await fetchBuyers();
    const buyerSel = document.getElementById('buyer');
    buyerSel.innerHTML = '<option value=\"\">-- Pilih Pembeli --</option>' + buyers.map(b=>(
      `<option value=\"${b.id}\">${escapeHtml(b.name)}${b.phone_e164? ' ('+b.phone_e164+')':''}</option>`
    )).join('');
    if (cart.buyer){
      buyerSel.value = cart.buyer.id;
    }

    // set tanggal default
    document.getElementById('tgl').value = new Date().toISOString().slice(0,10);

    // render keranjang
    renderCart();
  }

  function setBuyerById(id){
    const b = buyers.find(x=>x.id===id) || null;
    cart.buyer = b; saveCart(cart);
  }

  function addItem(){
    const nama = document.getElementById('nama').value.trim();
    const beli = Number(document.getElementById('beli').value);
    const jual = Number(document.getElementById('jual').value);
    const qty  = Number(document.getElementById('qty').value || 1);
    if (!cart.buyer){ alert('Pilih pembeli dulu'); return; }
    if (!nama){ alert('Nama barang wajib'); return; }
    if (!(beli>=0 && jual>=0 && qty>0)){ alert('Cek angka HB/HJ/Qty'); return; }
    cart.items.push({ nama, beli, jual, qty, at: Date.now() });
    saveCart(cart);
    clearForm();
    renderCart();
  }

  function clearForm(){
    document.getElementById('nama').value='';
    document.getElementById('beli').value='';
    document.getElementById('jual').value='';
    document.getElementById('qty').value=1;
    document.getElementById('nama').focus();
  }

  function delItem(idx){
    cart.items.splice(idx,1); saveCart(cart); renderCart();
  }

  function renderCart(){
    const tbody = document.getElementById('tbody');
    tbody.innerHTML='';
    let i=0;
    for (const it of cart.items){
      const penjualan = it.jual*it.qty, modal=it.beli*it.qty, laba=penjualan-modal;
      const tr = document.createElement('tr');
      tr.className = (i++%2)?'bg-white':'bg-gray-50';
      tr.innerHTML = `
        <td class=\"py-2 px-2\">${escapeHtml(it.nama)}</td>
        <td class=\"py-2 px-2 text-right\">${fmtIDR.format(it.beli)}</td>
        <td class=\"py-2 px-2 text-right\">${fmtIDR.format(it.jual)}</td>
        <td class=\"py-2 px-2 text-right\">${it.qty}</td>
        <td class=\"py-2 px-2 text-right\">${fmtIDR.format(penjualan)}</td>
        <td class=\"py-2 px-2 text-right\">${fmtIDR.format(modal)}</td>
        <td class=\"py-2 px-2 text-right font-semibold ${laba>=0?'text-emerald-600':'text-rose-600'}\">${fmtIDR.format(laba)}</td>
        <td class=\"py-2 px-2 text-right\">
          <button class=\"text-xs px-2 py-1 border rounded hover:bg-gray-100\" onclick=\"delItem(${i-1})\">Hapus</button>
        </td>`;
      tbody.appendChild(tr);
    }
    const {total, modal, laba} = calcTotals(cart.items);
    document.getElementById('sumPenjualan').textContent = fmtIDR.format(total);
    document.getElementById('sumModal').textContent     = fmtIDR.format(modal);
    document.getElementById('sumLaba').textContent      = fmtIDR.format(laba);
    document.getElementById('btnBayar').disabled = !cart.items.length || !cart.buyer;
  }

  async function openBayar(){
    if (!cart.items.length){ alert('Keranjang masih kosong'); return; }
    if (!cart.buyer){ alert('Pilih pembeli'); return; }
    const { total } = calcTotals(cart.items);
    document.getElementById('totalBayar').textContent = fmtIDR.format(total);
    document.getElementById('inputBayar').value = total;
    document.getElementById('kembalian').textContent = 'Rp 0';
    document.getElementById('dlg').showModal();
  }

  function onBayarInput(){
    const bayar = Number(document.getElementById('inputBayar').value||0);
    const { total } = calcTotals(cart.items);
    const kembali = Math.max(0, bayar-total);
    document.getElementById('kembalian').textContent = fmtIDR.format(kembali);
  }

  async function confirmBayar(){
    const bayar = Number(document.getElementById('inputBayar').value||0);
    const { total } = calcTotals(cart.items);
    if (bayar < total){ alert('Jumlah bayar kurang dari total'); return; }

    const order = {
      tgl: document.getElementById('tgl').value,
      buyer: cart.buyer,
      items: cart.items,
      total, paid_amount: bayar, change: bayar-total
    };
    localStorage.setItem(ORDER_KEY, JSON.stringify(order));

    // 1) Kirim ke server untuk laporan
    try {
      await fetch('/api/sales', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(order)
      });
    } catch(e) { console.warn('save sale failed', e); }

    // 2) Stub kirim WA
    try {
      await fetch('/api/wa', {
        method: 'POST',
        headers: { 'Content-Type':'application/json' },
        body: JSON.stringify({
          to: cart.buyer?.phone_e164 || null,
          buyer_name: cart.buyer?.name || 'Pembeli',
          message: `Terima kasih ${cart.buyer?.name||''}. Total belanja: ${fmtIDR.format(total)}.`
        })
      });
    } catch(e){ console.warn('WA send failed (demo):', e); }

    cart.items = [];
    saveCart(cart);
    renderCart();
    document.getElementById('dlg').close();
    alert('Pembayaran berhasil & tersimpan (demo).');
  }
  
  </script>
</head>
<body class="bg-gray-50 text-gray-900">
  <header class="sticky top-0 z-10 bg-white/90 backdrop-blur border-b">
    <div class="max-w-screen-sm mx-auto px-4 py-3 flex items-center justify-between">
      <div class="font-medium">Penjualan</div>
      <div class="flex items-center gap-3">
        <button onclick="logout()" class="text-sm px-3 py-1.5 rounded border hover:bg-gray-100">Keluar</button>
      </div>
    </div>
  </header>

  <main class="max-w-screen-sm mx-auto px-4 py-4 space-y-6">
    <!-- Pilih Pembeli -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Pembeli</h2>
      <div class="grid grid-cols-1 gap-3">
        <div>
          <label class="block text-sm mb-1">Tanggal</label>
          <input id="tgl" type="date" class="w-full px-3 py-2 border rounded" />
        </div>
        <div>
          <label class="block text-sm mb-1">Pilih Pembeli</label>
          <select id="buyer" class="w-full px-3 py-2 border rounded" onchange="setBuyerById(this.value)"></select>
        </div>
      </div>
    </section>

    <!-- Form Item -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Tambah Barang</h2>
      <div class="grid grid-cols-2 gap-3">
        <div class="col-span-2">
          <label class="block text-sm mb-1">Nama Barang</label>
          <input id="nama" type="text" placeholder="Contoh: Indomie Goreng" class="w-full px-3 py-2 border rounded" />
        </div>
        <div>
          <label class="block text-sm mb-1">Harga Beli /unit</label>
          <input id="beli" type="number" inputmode="numeric" min="0" step="1" class="w-full px-3 py-2 border rounded" />
        </div>
        <div>
          <label class="block text-sm mb-1">Harga Jual /unit</label>
          <input id="jual" type="number" inputmode="numeric" min="0" step="1" class="w-full px-3 py-2 border rounded" />
        </div>
        <div>
          <label class="block text-sm mb-1">Qty</label>
          <input id="qty" type="number" inputmode="numeric" min="1" step="1" value="1" class="w-full px-3 py-2 border rounded" />
        </div>
        <div class="flex items-end">
          <button onclick="addItem()" class="w-full px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-700">Tambah</button>
        </div>
      </div>
      <p class="text-xs text-gray-500">Rumus laba: (Harga Jual − Harga Beli) × Qty</p>
    </section>

    <!-- Tabel Keranjang -->
    <section class="bg-white rounded-lg shadow-sm p-2">
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-100">
            <tr class="text-left">
              <th class="py-2 px-2">Barang</th>
              <th class="py-2 px-2 text-right">HB</th>
              <th class="py-2 px-2 text-right">HJ</th>
              <th class="py-2 px-2 text-right">Qty</th>
              <th class="py-2 px-2 text-right">Penjualan</th>
              <th class="py-2 px-2 text-right">Modal</th>
              <th class="py-2 px-2 text-right">Laba</th>
              <th class="py-2 px-2"></th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </section>

    <!-- Ringkasan + Bayar -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Ringkasan</h2>
      <div class="grid grid-cols-3 gap-3 text-sm">
        <div class="p-3 border rounded">
          <div class="text-gray-500">Total Penjualan</div>
          <div id="sumPenjualan" class="font-semibold">Rp 0</div>
        </div>
        <div class="p-3 border rounded">
          <div class="text-gray-500">Total Modal</div>
          <div id="sumModal" class="font-semibold">Rp 0</div>
        </div>
        <div class="p-3 border rounded">
          <div class="text-gray-500">Total Laba</div>
          <div id="sumLaba" class="font-semibold">Rp 0</div>
        </div>
      </div>

      <button id="btnBayar" onclick="openBayar()" class="w-full py-3 rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50" disabled>Bayar</button>
    </section>
  </main>

  <!-- Modal Bayar -->
  <dialog id="dlg" class="rounded-lg w-full max-w-sm">
    <form method="dialog" class="p-4 space-y-4">
      <h3 class="text-lg font-semibold">Pembayaran</h3>
      <div class="text-sm">
        <div class="text-gray-500">Total</div>
        <div id="totalBayar" class="font-semibold">Rp 0</div>
      </div>
      <div>
        <label class="block text-sm mb-1">Jumlah Bayar</label>
        <input id="inputBayar" type="number" inputmode="numeric" min="0" step="1" class="w-full px-3 py-2 border rounded" oninput="onBayarInput()" />
      </div>
      <div class="text-sm">
        <div class="text-gray-500">Kembalian</div>
        <div id="kembalian" class="font-semibold">Rp 0</div>
      </div>
      <div class="flex gap-2">
        <button class="flex-1 py-2 rounded border" value="cancel">Batal</button>
        <button type="button" class="flex-1 py-2 rounded bg-blue-600 text-white hover:bg-blue-700" onclick="confirmBayar()">Konfirmasi</button>
      </div>
    </form>
  </dialog>

  <script>init();</script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

@app.route("/pemodal")
def pemodal_page():
    html = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Pemodal</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
  const SESS_KEY='kelontong_sess_v1';
  function getSession(){ try{ return JSON.parse(localStorage.getItem(SESS_KEY)) || null }catch{return null} }
  function logout(){ localStorage.removeItem(SESS_KEY); location.href='/login'; }
  (function guard(){ if(!getSession()?.user) location.href='/login'; })();

  async function loadInvestors(){
    const y = document.getElementById('filterYear').value;
    const url = y ? '/api/investors?year='+encodeURIComponent(y) : '/api/investors';
    const r = await fetch(url); const js = await r.json();
    renderTable(js.items||[]); renderSummary(js.summary||[]);
  }
  function renderTable(items){
    const tb = document.getElementById('tbody'); tb.innerHTML='';
    items.forEach((it,idx)=>{
      const tr = document.createElement('tr');
      tr.className = idx%2?'bg-white':'bg-gray-50';
      tr.innerHTML = `
        <td class="py-2 px-2">${it.name}</td>
        <td class="py-2 px-2">${it.year}</td>
        <td class="py-2 px-2 text-right">${new Intl.NumberFormat('id-ID').format(it.amount_idr)}</td>
        <td class="py-2 px-2">${it.note||''}</td>
        <td class="py-2 px-2 text-right">
          <button class="text-xs px-2 py-1 border rounded hover:bg-gray-100" onclick="delInvestor('${it.id}')">Hapus</button>
        </td>`;
      tb.appendChild(tr);
    });
  }
  function renderSummary(rows){
    const sb = document.getElementById('sumBody'); sb.innerHTML='';
    rows.forEach((r)=>{
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="py-2 px-2">${r.year}</td>
        <td class="py-2 px-2 text-right">${r.count}</td>
        <td class="py-2 px-2 text-right">${new Intl.NumberFormat('id-ID').format(r.total_idr)}</td>`;
      sb.appendChild(tr);
    });
  }
  async function addInvestor(){
    const name = document.getElementById('name').value.trim();
    const year = Number(document.getElementById('year').value);
    const amount = Number(document.getElementById('amount').value||0);
    const note = document.getElementById('note').value.trim();
    if(!name || !year){ alert('Nama & Tahun wajib'); return; }
    await fetch('/api/investors', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, year, amount_idr: amount, note})
    });
    document.getElementById('name').value='';
    document.getElementById('amount').value='';
    document.getElementById('note').value='';
    loadInvestors();
  }
  async function delInvestor(id){
    if(!confirm('Hapus data ini?')) return;
    await fetch('/api/investors?id='+encodeURIComponent(id), {method:'DELETE'});
    loadInvestors();
  }
  </script>
</head>
<body class="bg-gray-50 text-gray-900">
  <header class="sticky top-0 z-10 bg-white/90 backdrop-blur border-b">
    <div class="max-w-screen-sm mx-auto px-4 py-3 flex items-center justify-between">
      <div class="font-medium">Pemodal</div>
      <div><button onclick="logout()" class="text-sm px-3 py-1.5 rounded border hover:bg-gray-100">Keluar</button></div>
    </div>
  </header>

  <main class="max-w-screen-sm mx-auto px-4 py-4 space-y-6">
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Tambah Pemodal</h2>
      <div class="grid grid-cols-2 gap-3">
        <div class="col-span-2">
          <label class="block text-sm mb-1">Nama</label>
          <input id="name" class="w-full px-3 py-2 border rounded" placeholder="Nama Pemodal" />
        </div>
        <div>
          <label class="block text-sm mb-1">Tahun</label>
          <input id="year" type="number" min="2000" max="2100" class="w-full px-3 py-2 border rounded" value="2025" />
        </div>
        <div>
          <label class="block text-sm mb-1">Jumlah (Rp)</label>
          <input id="amount" type="number" min="0" step="1000" class="w-full px-3 py-2 border rounded" />
        </div>
        <div class="col-span-2">
          <label class="block text-sm mb-1">Catatan</label>
          <input id="note" class="w-full px-3 py-2 border rounded" />
        </div>
        <div class="col-span-2">
          <button onclick="addInvestor()" class="w-full py-2 rounded bg-blue-600 text-white hover:bg-blue-700">Simpan</button>
        </div>
      </div>
    </section>

    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <div class="flex items-end gap-3">
        <div class="flex-1">
          <label class="block text-sm mb-1">Filter Tahun</label>
          <input id="filterYear" type="number" min="2000" max="2100" class="w-full px-3 py-2 border rounded" placeholder="Kosongkan untuk semua" />
        </div>
        <button onclick="loadInvestors()" class="px-4 py-2 rounded border hover:bg-gray-100">Terapkan</button>
      </div>

      <h2 class="font-medium mt-2">Daftar Pemodal</h2>
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-100">
            <tr class="text-left">
              <th class="py-2 px-2">Nama</th>
              <th class="py-2 px-2">Tahun</th>
              <th class="py-2 px-2 text-right">Jumlah (Rp)</th>
              <th class="py-2 px-2">Catatan</th>
              <th class="py-2 px-2"></th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </section>

    <section class="bg-white rounded-lg shadow-sm p-4">
      <h2 class="font-medium mb-2">Ringkasan per Tahun</h2>
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-100">
            <tr class="text-left">
              <th class="py-2 px-2">Tahun</th>
              <th class="py-2 px-2 text-right">Jumlah Pemodal</th>
              <th class="py-2 px-2 text-right">Total Uang (Rp)</th>
            </tr>
          </thead>
          <tbody id="sumBody"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>loadInvestors();</script>
</body>
</html>"""
    resp = make_response(html); resp.headers["Content-Type"]="text/html; charset=utf-8"; return resp

@app.route("/laporan")
def laporan_page():
    html = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Laporan</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
  const SESS_KEY='kelontong_sess_v1';
  function getSession(){ try{ return JSON.parse(localStorage.getItem(SESS_KEY)) || null }catch{return null} }
  function logout(){ localStorage.removeItem(SESS_KEY); location.href='/login'; }
  (function guard(){ if(!getSession()?.user) location.href='/login'; })();

  function fmtIDR(v){ return new Intl.NumberFormat('id-ID', {style:'currency', currency:'IDR', maximumFractionDigits:0}).format(v||0); }

  async function loadBagiHasil(){
    const f=document.getElementById('from').value, t=document.getElementById('to').value;
    const qs = new URLSearchParams(); if(f) qs.set('from',f); if(t) qs.set('to',t);
    const r=await fetch('/api/reports/profit-sharing?'+qs.toString()); const js=await r.json();
    document.getElementById('total_laba').textContent   = fmtIDR(js.total_profit||0);
    document.getElementById('share_karyawan').textContent= fmtIDR(js.karyawan||0);
    document.getElementById('share_pemodal').textContent = fmtIDR(js.pemodal||0);
    document.getElementById('share_kas').textContent     = fmtIDR(js.kas||0);
  }

  async function loadRekap(){
    const f=document.getElementById('from2').value, t=document.getElementById('to2').value;
    const qs = new URLSearchParams(); if(f) qs.set('from',f); if(t) qs.set('to',t);
    const r=await fetch('/api/reports/sales-by-day?'+qs.toString()); const js=await r.json();
    const tb=document.getElementById('tbody'); tb.innerHTML='';
    (js.items||[]).forEach((row,idx)=>{
      const tr=document.createElement('tr');
      tr.className = idx%2?'bg-white':'bg-gray-50';
      tr.innerHTML = `
        <td class="py-2 px-2">${row.day}</td>
        <td class="py-2 px-2 text-right">${row.trx_count}</td>
        <td class="py-2 px-2 text-right">${fmtIDR(row.total_penjualan)}</td>
        <td class="py-2 px-2 text-right">${fmtIDR(row.total_modal)}</td>
        <td class="py-2 px-2 text-right">${fmtIDR(row.total_laba)}</td>`;
      tb.appendChild(tr);
    });
  }

  function setDefaultDates(){
    const today = new Date().toISOString().slice(0,10);
    document.getElementById('from').value = today;
    document.getElementById('to').value   = today;
    document.getElementById('from2').value = today;
    document.getElementById('to2').value   = today;
  }
  </script>
</head>
<body class="bg-gray-50 text-gray-900">
  <header class="sticky top-0 z-10 bg-white/90 backdrop-blur border-b">
    <div class="max-w-screen-sm mx-auto px-4 py-3 flex items-center justify-between">
      <div class="font-medium">Laporan</div>
      <div><button onclick="logout()" class="text-sm px-3 py-1.5 rounded border hover:bg-gray-100">Keluar</button></div>
    </div>
  </header>

  <main class="max-w-screen-sm mx-auto px-4 py-4 space-y-6">
    <!-- Bagi Hasil -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Bagi Hasil (30%/35%/35%)</h2>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-sm mb-1">Dari</label>
          <input id="from" type="date" class="w-full px-3 py-2 border rounded" />
        </div>
        <div>
          <label class="block text-sm mb-1">Sampai</label>
          <input id="to" type="date" class="w-full px-3 py-2 border rounded" />
        </div>
      </div>
      <button onclick="loadBagiHasil()" class="mt-2 px-4 py-2 rounded border hover:bg-gray-100">Terapkan</button>

      <div class="grid grid-cols-3 gap-3 text-sm mt-4">
        <div class="p-3 border rounded">
          <div class="text-gray-500">Total Laba</div>
          <div id="total_laba" class="font-semibold">Rp 0</div>
        </div>
        <div class="p-3 border rounded">
          <div class="text-gray-500">Karyawan (30%)</div>
          <div id="share_karyawan" class="font-semibold">Rp 0</div>
        </div>
        <div class="p-3 border rounded">
          <div class="text-gray-500">Pemodal (35%)</div>
          <div id="share_pemodal" class="font-semibold">Rp 0</div>
        </div>
        <div class="p-3 border rounded col-span-3 md:col-span-1">
          <div class="text-gray-500">Kas (35%)</div>
          <div id="share_kas" class="font-semibold">Rp 0</div>
        </div>
      </div>
    </section>

    <!-- Rekap Penjualan Harian -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Rekap Penjualan Harian</h2>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-sm mb-1">Dari</label>
          <input id="from2" type="date" class="w-full px-3 py-2 border rounded" />
        </div>
        <div>
          <label class="block text-sm mb-1">Sampai</label>
          <input id="to2" type="date" class="w-full px-3 py-2 border rounded" />
        </div>
      </div>
      <button onclick="loadRekap()" class="mt-2 px-4 py-2 rounded border hover:bg-gray-100">Terapkan</button>

      <div class="overflow-x-auto mt-3">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-100">
            <tr class="text-left">
              <th class="py-2 px-2">Tanggal</th>
              <th class="py-2 px-2 text-right">Trx</th>
              <th class="py-2 px-2 text-right">Penjualan</th>
              <th class="py-2 px-2 text-right">Modal</th>
              <th class="py-2 px-2 text-right">Laba</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    setDefaultDates();
    loadBagiHasil();
    loadRekap();
  </script>
</body>
</html>"""
    resp = make_response(html); resp.headers["Content-Type"]="text/html; charset=utf-8"; return resp

@app.route("/pembeli")
def pembeli_page():
    html = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Pembeli</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
  // ===== Auth guard FE-only =====
  const SESS_KEY='kelontong_sess_v1';
  function getSession(){ try{ return JSON.parse(localStorage.getItem(SESS_KEY)) || null }catch{return null} }
  function logout(){ localStorage.removeItem(SESS_KEY); location.href='/login'; }
  (function guard(){ if(!getSession()?.user) location.href='/login'; })();

  // ===== Helpers =====
  function fmtPhone(p){ return p || '-'; }

  // ===== API calls =====
  async function loadBuyers(){
    const q = document.getElementById('q').value.trim();
    const url = q ? '/api/buyers?q='+encodeURIComponent(q) : '/api/buyers';
    const r = await fetch(url);
    const items = await r.json();
    renderTable(items);
  }

  async function addBuyer(){
    const name  = document.getElementById('name').value.trim();
    const phone = document.getElementById('phone').value.trim();
    const note  = document.getElementById('note').value.trim();
    const wa    = document.getElementById('wa').checked;
    if(!name){ alert('Nama wajib diisi'); return; }
    const r = await fetch('/api/buyers', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, phone_e164: phone || null, note, wa_opt_in: wa})
    });
    const js = await r.json();
    if(!js.ok){ alert(js.error||'Gagal simpan'); return; }
    // reset form
    document.getElementById('name').value='';
    document.getElementById('phone').value='';
    document.getElementById('note').value='';
    document.getElementById('wa').checked=true;
    loadBuyers();
  }

  async function delBuyer(id){
    if(!confirm('Hapus pembeli ini?')) return;
    await fetch('/api/buyers?id='+encodeURIComponent(id), { method:'DELETE' });
    loadBuyers();
  }

  function renderTable(items){
    const tb = document.getElementById('tbody'); tb.innerHTML='';
    items.forEach((b, i)=>{
      const tr = document.createElement('tr');
      tr.className = i%2 ? 'bg-white':'bg-gray-50';
      tr.innerHTML = `
        <td class="py-2 px-2">${b.name}</td>
        <td class="py-2 px-2">${fmtPhone(b.phone_e164)}</td>
        <td class="py-2 px-2">${b.note||''}</td>
        <td class="py-2 px-2">${b.wa_opt_in===false?'Tidak':'Ya'}</td>
        <td class="py-2 px-2 text-right">
          <button class="text-xs px-2 py-1 border rounded hover:bg-gray-100" onclick="delBuyer('${b.id}')">Hapus</button>
        </td>`;
      tb.appendChild(tr);
    });
    document.getElementById('count').textContent = items.length;
  }
  </script>
</head>
<body class="bg-gray-50 text-gray-900">
  <header class="sticky top-0 z-10 bg-white/90 backdrop-blur border-b">
    <div class="max-w-screen-sm mx-auto px-4 py-3 flex items-center justify-between">
      <div class="font-medium">Pembeli</div>
      <nav class="hidden sm:block text-sm text-gray-600">
        <a class="mr-3 hover:underline" href="/penjualan">Penjualan</a>
        <a class="mr-3 hover:underline" href="/pembeli">Pembeli</a>
        <a class="mr-3 hover:underline" href="/pemodal">Pemodal</a>
        <a class="hover:underline" href="/laporan">Laporan</a>
      </nav>
      <div><button onclick="logout()" class="text-sm px-3 py-1.5 rounded border hover:bg-gray-100">Keluar</button></div>
    </div>
  </header>

  <main class="max-w-screen-sm mx-auto px-4 py-4 space-y-6">
    <!-- Form Tambah Pembeli -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <h2 class="font-medium">Tambah Pembeli</h2>
      <div class="grid grid-cols-2 gap-3">
        <div class="col-span-2">
          <label class="block text-sm mb-1">Nama</label>
          <input id="name" class="w-full px-3 py-2 border rounded" placeholder="Nama pembeli" />
        </div>
        <div>
          <label class="block text-sm mb-1">Telepon (E.164)</label>
          <input id="phone" class="w-full px-3 py-2 border rounded" placeholder="+62812xxxxxxx" />
        </div>
        <div>
          <label class="block text-sm mb-1">Terima WA?</label>
          <label class="inline-flex items-center gap-2">
            <input id="wa" type="checkbox" class="accent-blue-600" checked />
            <span>Ya, kirim info lewat WhatsApp</span>
          </label>
        </div>
        <div class="col-span-2">
          <label class="block text-sm mb-1">Catatan</label>
          <input id="note" class="w-full px-3 py-2 border rounded" placeholder="Opsional" />
        </div>
        <div class="col-span-2">
          <button onclick="addBuyer()" class="w-full py-2 rounded bg-blue-600 text-white hover:bg-blue-700">Simpan</button>
        </div>
      </div>
    </section>

    <!-- Pencarian -->
    <section class="bg-white rounded-lg shadow-sm p-4 space-y-3">
      <div class="flex gap-2">
        <input id="q" class="flex-1 px-3 py-2 border rounded" placeholder="Cari nama/telepon..." />
        <button onclick="loadBuyers()" class="px-4 py-2 rounded border hover:bg-gray-100">Cari</button>
      </div>
      <div class="text-xs text-gray-500">Total data: <span id="count">0</span></div>
    </section>

    <!-- Tabel Pembeli -->
    <section class="bg-white rounded-lg shadow-sm p-2">
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-100">
            <tr class="text-left">
              <th class="py-2 px-2">Nama</th>
              <th class="py-2 px-2">Telepon</th>
              <th class="py-2 px-2">Catatan</th>
              <th class="py-2 px-2">WA</th>
              <th class="py-2 px-2"></th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    loadBuyers();
  </script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

# ====== API demo ======
# ====== API: Buyers ======
@app.route("/api/buyers", methods=["GET", "POST", "DELETE"])
def api_buyers():
    # GET ?q=keyword → cari nama/telepon (opsional)
    if request.method == "GET":
        q = (request.args.get("q") or "").strip().lower()
        if not q:
            return jsonify(DEMO_BUYERS)
        res = []
        for b in DEMO_BUYERS:
            s = f"{b.get('name','')} {b.get('phone_e164','')}".lower()
            if q in s:
                res.append(b)
        return jsonify(res)

    # POST {name, phone_e164, note, wa_opt_in} → tambah pembeli
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        phone = (body.get("phone_e164") or "").strip() or None
        note = body.get("note") or ""
        wa_opt_in = bool(body.get("wa_opt_in", True))
        if not name:
            return jsonify({"ok": False, "error": "Nama wajib diisi"}), 400
        new_id = f"b-{len(DEMO_BUYERS)+1}"
        DEMO_BUYERS.append({
            "id": new_id, "name": name, "phone_e164": phone,
            "note": note, "wa_opt_in": wa_opt_in
        })
        return jsonify({"ok": True, "item": {"id": new_id}})

    # DELETE ?id=... → hapus pembeli
    if request.method == "DELETE":
        bid = request.args.get("id")
        before = len(DEMO_BUYERS)
        DEMO_BUYERS[:] = [b for b in DEMO_BUYERS if b.get("id") != bid]
        return jsonify({"ok": True, "deleted": before - len(DEMO_BUYERS)})


@app.route("/api/wa", methods=["POST"])
def api_wa():
    data = request.get_json(silent=True) or {}
    # Di sini nanti ganti dengan call WA provider.
    # Untuk demo, kita log saja:
    print("[WA DEMO] to:", data.get("to"), "buyer:", data.get("buyer_name"), "msg:", data.get("message"))
    return jsonify({"ok": True})

# ====== API: Investors ======
@app.route("/api/investors", methods=["GET", "POST", "DELETE"])
def api_investors():
    if request.method == "GET":
        year = request.args.get("year", type=int)
        if year:
            data = [x for x in DEMO_INVESTORS if x["year"] == year]
        else:
            data = DEMO_INVESTORS
        # ringkasan per tahun
        summary = {}
        for x in DEMO_INVESTORS:
            y = x["year"]
            summary.setdefault(y, {"year": y, "count": 0, "total_idr": 0})
            summary[y]["count"] += 1
            summary[y]["total_idr"] += int(x["amount_idr"])
        return jsonify({"items": data, "summary": sorted(summary.values(), key=lambda v: v["year"], reverse=True)})

    if request.method == "POST":
        body = request.get_json() or {}
        name = (body.get("name") or "").strip()
        year = int(body.get("year") or 0)
        amount = int(body.get("amount_idr") or 0)
        note = body.get("note") or ""
        if not name or year < 2000 or amount < 0:
            return jsonify({"ok": False, "error": "Data investor tidak valid"}), 400
        inv = {"id": _gen_id("investor"), "name": name, "year": year, "amount_idr": amount, "note": note}
        DEMO_INVESTORS.append(inv)
        return jsonify({"ok": True, "item": inv})

    if request.method == "DELETE":
        inv_id = request.args.get("id")
        before = len(DEMO_INVESTORS)
        DEMO_INVESTORS[:] = [x for x in DEMO_INVESTORS if x["id"] != inv_id]
        return jsonify({"ok": True, "deleted": before - len(DEMO_INVESTORS)})

# ====== API: Sales & Reports ======
@app.route("/api/sales", methods=["POST"])
def api_sales_create():
    # terima order dari halaman penjualan
    body = request.get_json() or {}
    sale_date = body.get("tgl")
    buyer = body.get("buyer")
    items = body.get("items") or []
    paid_amount = int(body.get("paid_amount") or 0)

    if not sale_date or not items:
        return jsonify({"ok": False, "error": "Data transaksi tidak lengkap"}), 400

    # hitung total
    total_amount = sum(int(it["jual"]) * int(it["qty"]) for it in items)
    total_cost   = sum(int(it["beli"]) * int(it["qty"]) for it in items)
    total_profit = total_amount - total_cost
    change = max(0, paid_amount - total_amount)

    sale = {
        "id": _gen_id("sale"),
        "sale_date": sale_date,
        "buyer": buyer,
        "items": items,
        "total_amount": total_amount,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "paid_amount": paid_amount,
        "change": change,
    }
    SALES_MEM.append(sale)
    return jsonify({"ok": True, "sale": sale})

# Bagi hasil: 30% karyawan, 35% pemodal, 35% kas
@app.route("/api/reports/profit-sharing")
def api_profit_sharing():
    from_date = request.args.get("from")
    to_date   = request.args.get("to")
    def in_range(s):
        return (not from_date or s["sale_date"] >= from_date) and (not to_date or s["sale_date"] <= to_date)

    total_laba = sum(s["total_profit"] for s in SALES_MEM if in_range(s))
    return jsonify({
        "from": from_date, "to": to_date,
        "total_profit": total_laba,
        "karyawan": total_laba * 30 // 100,
        "pemodal":  total_laba * 35 // 100,
        "kas":      total_laba * 35 // 100
    })

# Rekap harian
@app.route("/api/reports/sales-by-day")
def api_sales_by_day():
    from_date = request.args.get("from")
    to_date   = request.args.get("to")
    def in_range(s):
        return (not from_date or s["sale_date"] >= from_date) and (not to_date or s["sale_date"] <= to_date)

    rows = {}
    for s in SALES_MEM:
        if not in_range(s): 
            continue
        d = s["sale_date"]
        rows.setdefault(d, {"day": d, "trx_count": 0, "total_penjualan": 0, "total_modal": 0, "total_laba": 0})
        rows[d]["trx_count"] += 1
        rows[d]["total_penjualan"] += s["total_amount"]
        rows[d]["total_modal"]     += s["total_cost"]
        rows[d]["total_laba"]      += s["total_profit"]

    data = sorted(rows.values(), key=lambda x: x["day"])
    return jsonify({"items": data})

if __name__ == "__main__":
    # Jalankan Flask dev server
    app.run(host="0.0.0.0", port=5000, debug=True)
