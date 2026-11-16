import os,json,sqlite3,time,math,re,uuid,logging,hmac,gzip
from datetime import datetime,timezone
from decimal import Decimal
from contextlib import contextmanager
from urllib.request import Request,urlopen
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).parent
DB=os.environ.get('SHOP_DB',str(ROOT/'data/shop.db'))
def now(): return datetime.now(timezone.utc).isoformat()
@contextmanager
def db():
 c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row
 try:
  with c:yield c
 finally:c.close()
def init():
 Path(DB).parent.mkdir(parents=True,exist_ok=True)
 with db() as c:
  c.execute('PRAGMA journal_mode=WAL')
  c.executescript('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,brand TEXT,data TEXT,seen TEXT); CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,data TEXT,created TEXT,status TEXT);')
def state(key,default=None):
 with db() as c:r=c.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
 return json.loads(r[0]) if r else default
def save(key,value):
 with db() as c:c.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,json.dumps(value)))
def fetch(url):
 with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; TurkeyShop/1.0)'}),timeout=35) as r:return r.read().decode()
def walk(x):
 yield x
 if isinstance(x,dict):
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def parse_products(html,brand):
 found={}
 for script in re.findall(r'<script[^>]*>(.*?)</script>',html,re.S):
  try:data=json.loads(script)
  except ValueError:continue
  for p in walk(data):
   if not isinstance(p,dict):continue
   if brand=='Nike' and p.get('productType')=='FOOTWEAR' and p.get('prices',{}).get('currency')=='TRY':
    item={'id':'nike-'+p['productCode'],'brand':brand,'name':p['copy']['title'],'category':p['copy'].get('subTitle',''),'try':p['prices']['currentPrice'],'image':p.get('colorwayImages',{}).get('squarishURL',''),'url':p['pdpUrl']['url']}
   elif brand=='Adidas' and p.get('@type')=='Product':
    offers=p.get('offers',{});offers=offers[0] if isinstance(offers,list) and offers else offers
    if not isinstance(offers,dict) or offers.get('priceCurrency')!='TRY':continue
    img=p.get('image','');img=img[0] if isinstance(img,list) and img else img
    item={'id':'adidas-'+str(p.get('sku') or p.get('productID')),'brand':brand,'name':p['name'],'category':'Ayakkabı','try':float(offers['price']),'image':img,'url':p.get('url',offers.get('url',''))}
   else:continue
   if item['try']>0 and item['url'].startswith('https://') and isinstance(item['image'],str):found[item['id']]=item
 return list(found.values())
def sync_prices():
 status=state('sync',{})
 for name,url in [('rate','https://parisan.shop/rate.json'),('shipping','https://parisan.shop/shipping-rates.json')]:
  try:
   data=json.loads(fetch(url))
   if name=='rate' and not 0<float(data['rate'])<10000000:raise ValueError('Invalid rate')
   if name=='shipping' and (data.get('unit')!='toman_per_kg' or not data.get('options')):raise ValueError('Invalid tariffs')
   save(name,{'data':data,'fetched':now()});status[name]={'ok':True,'at':now()}
  except Exception as e:status[name]={'ok':False,'at':now(),'error':str(e)}
 save('sync',status)
def sync():
 from importer import init as import_init, catalog_brand
 sync_prices();import_init();status=state('sync',{})
 from importer import BRANDS
 for brand in BRANDS:
  _,status[brand]=catalog_brand(brand)
 save('sync',status)
def fresh(at,seconds=86400):
 try:return (datetime.now(timezone.utc)-datetime.fromisoformat(at)).total_seconds()<seconds
 except (TypeError,ValueError):return False
def rounded_total(goods,shipping):
 return math.ceil((Decimal(str(goods))*Decimal('1.10')+Decimal(str(shipping)))/Decimal(1000))*1000
def estimate(p,feeds=None):
 rate,ship=feeds if feeds is not None else (state('rate'),state('shipping'))
 if not rate or not ship or not fresh(rate['fetched']) or not fresh(rate['data'].get('updated_at')) or not fresh(ship['fetched']):return None
 option=next((s for s in ship['data'].get('options',[]) if s['id']=='air_apparel'),None)
 if not option or not option.get('rate_per_kg'):return None
 goods=p['try']*rate['data']['rate'];shipping=p.get('estimated_weight_g',1000)/1000*option['rate_per_kg']
 return {'goods':round(goods),'commission':round(goods*.1),'shipping':round(shipping),'total':rounded_total(goods,shipping),'weight_g':p.get('estimated_weight_g',1000),'currency':'toman'}
def orderable(p):
 return (p.get('listed',True) and p.get('availability')!='out_of_stock'
         and fresh(p.get('stock_checked'),43200)
         and any(s.get('available') is True for s in p.get('sizes',[])))
def public_product(row,summary=False,feeds=None):
 p=json.loads(row['data']);p['updated']=row['seen'];p['estimate']=estimate(p,feeds)
 p['stock_fresh']=fresh(p.get('stock_checked'),43200);p['orderable']=orderable(p)
 if not p['stock_fresh']:
  p['sizes']=[dict(s,available=None) for s in p.get('sizes',[])]
  if p.get('availability')!='out_of_stock':p['availability']='unknown'
 if summary:
  keys=['id','sku','brand','name','category','try','image','local_image','availability','stock_fresh','orderable','estimate','updated','color']
  p={k:p.get(k) for k in keys}
 return p
def quote(items,method='air_apparel',grams=1000):
 rate=state('rate');shipping=state('shipping')
 if not rate or not shipping:raise ValueError('Pricing temporarily unavailable')
 for record in (rate,shipping):
  if (datetime.now(timezone.utc)-datetime.fromisoformat(record['fetched'])).total_seconds()>86400:raise ValueError('Pricing feed is stale; please request confirmation')
 if (datetime.now(timezone.utc)-datetime.fromisoformat(rate['data']['updated_at'])).total_seconds()>86400:raise ValueError('Exchange rate is stale; please request confirmation')
 option=next((o for o in shipping['data']['options'] if o['id']==method and o['id'] in ('air_apparel','ground_apparel')),None)
 if not option or not option.get('rate_per_kg'):raise ValueError('Invalid shipping method')
 if not 200<=grams<=10000:raise ValueError('Weight must be 200–10000 grams per pair')
 goods=0;count=0;lines=[]
 with db() as c:
  for i in items:
   qty=int(i.get('qty',1))
   if not 1<=qty<=5:raise ValueError('Quantity must be 1–5')
   r=c.execute('SELECT * FROM products WHERE id=?',(i['id'],)).fetchone()
   if not r:raise ValueError('Product unavailable')
   if (datetime.now(timezone.utc)-datetime.fromisoformat(r['seen'])).total_seconds()>86400:raise ValueError('Product needs availability confirmation')
   p=json.loads(r['data'])
   if not p.get('listed',True) or p.get('availability')=='out_of_stock':raise ValueError('Product is out of stock')
   if not fresh(p.get('stock_checked'),43200):raise ValueError('Stock is unverified or stale; ordering is disabled')
   chosen=str(i.get('size','')).strip()
   if not chosen:raise ValueError('Please choose an available size')
   size=next((s for s in p.get('sizes',[]) if s['label']==chosen),None)
   if not size:raise ValueError('Please choose a listed size')
   if size.get('available') is False:raise ValueError('Selected size is out of stock')
   if size.get('available') is not True:raise ValueError('Selected size stock is unverified; ordering is disabled')
   if size.get('price_try') and float(size['price_try'])!=p['try']:p['try']=float(size['price_try'])
   goods+=p['try']*qty*rate['data']['rate'];count+=qty
   lines.append({'id':p['id'],'name':p['name'],'qty':qty,'size':str(i.get('size',''))[:30],'try':p['try']})
 if not 0<count<=10:raise ValueError('Choose between 1 and 10 pairs')
 fee=goods*.10;ship=count*grams/1000*option['rate_per_kg']
 return {'goods':round(goods),'commission':round(fee),'shipping':round(ship),'total':rounded_total(goods,ship),'currency':'toman','rate':rate['data']['rate'],'weight_g_per_pair':grams,'method':method,'items':lines,'estimated':True}
class Handler(BaseHTTPRequestHandler):
 def send(self,code,obj):
  data=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
  if len(data)>1024 and 'gzip' in self.headers.get('Accept-Encoding',''):
   data=gzip.compress(data,compresslevel=5);self.send_header('Content-Encoding','gzip')
  self.send_header('Vary','Accept-Encoding');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):
  path=self.path.split('?')[0]
  if path=='/api/health':return self.send(200,{'ok':True})
  if path=='/api/catalog':
   feeds=(state('rate'),state('shipping'))
   with db() as c:products=[public_product(r,True,feeds) for r in c.execute('SELECT * FROM products ORDER BY brand,id') if json.loads(r['data']).get('listed',True) and json.loads(r['data']).get('availability')!='out_of_stock']
   return self.send(200,{'products':products,'rate':state('rate'),'shipping':state('shipping'),'sync':state('sync',{})})
  if path.startswith('/api/products/'):
   productid=path.split('/')[-1]
   with db() as c:r=c.execute('SELECT * FROM products WHERE id=?',(productid,)).fetchone()
   if not r:return self.send(404,{'error':'Product not found'})
   return self.send(200,public_product(r))
  if path.startswith('/media/'):
   filename=path.rsplit('/',1)[-1]
   if not re.fullmatch(r'[a-zA-Z0-9_.-]+',filename):return self.send(404,{'error':'Not found'})
   file=Path(DB).parent/'media'/filename
   if not file.is_file():return self.send(404,{'error':'Not found'})
   data=file.read_bytes();mime='image/webp' if data[8:12]==b'WEBP' else 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
   self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Cache-Control','public, max-age=86400');self.end_headers();self.wfile.write(data);return
  if path=='/api/admin/orders':
   token=os.environ.get('ADMIN_TOKEN','')
   if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.send(401,{'error':'Authentication required'})
   with db() as c:orders=[dict(r) for r in c.execute('SELECT * FROM orders ORDER BY created DESC LIMIT 200')]
   return self.send(200,orders)
  if path.startswith('/products/'):
   with db() as c:exists=c.execute('SELECT 1 FROM products WHERE id=?',(path.split('/')[-1],)).fetchone()
   if not exists:return self.send(404,{'error':'Product not found'})
   path='/product.html'
  file=ROOT/'static'/({'/':'index.html','/admin':'admin.html'}.get(path,path.lstrip('/')))
  if not file.resolve().is_relative_to((ROOT/'static').resolve()) or not file.is_file():return self.send(404,{'error':'Not found'})
  data=file.read_bytes();self.send_response(200);self.send_header('Content-Type',{'html':'text/html; charset=utf-8','css':'text/css','js':'application/javascript','woff2':'font/ttf'}.get(file.suffix[1:],'application/octet-stream'));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-cache');self.end_headers();self.wfile.write(data)
 def do_POST(self):
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size>20000:return self.send(413,{'error':'Request too large'})
   body=json.loads(self.rfile.read(size));q=quote(body['items'],body.get('method','air_apparel'),float(body.get('grams',1000)))
   if self.path=='/api/quote':return self.send(200,q)
   if self.path!='/api/orders':return self.send(404,{'error':'Not found'})
   if not all(str(body.get(k,'')).strip() for k in ['name','phone','address']):raise ValueError('Name, phone and address required')
   if any(not i['size'] for i in q['items']):raise ValueError('Please enter a size for each pair')
   # Limit intake by IP; persist across restarts.
   key='intake:'+self.client_address[0];last=state(key,0)
   if time.time()-last<60:return self.send(429,{'error':'Please wait one minute before another request'})
   orderid=uuid.uuid4().hex[:12].upper();record={'quote':q,**{k:str(body[k]).strip()[:1000] for k in ['name','phone','address']}}
   with db() as c:c.execute('INSERT INTO orders VALUES (?,?,?,?)',(orderid,json.dumps(record,ensure_ascii=False),now(),'awaiting_confirmation'))
   save(key,time.time());return self.send(201,{'id':orderid,'status':'awaiting_confirmation'})
  except (ValueError,KeyError,TypeError) as e:return self.send(400,{'error':str(e)})
  except Exception:logging.exception('request failed');return self.send(500,{'error':'Please try again later'})
if __name__=='__main__':
 import sys
 init()
 if '--sync' in sys.argv:sync()
 else:ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT',8091))),Handler).serve_forever()
