"""Unattended Turkish shoe catalog discovery and cached product enrichment.
No customer request contacts a supplier. Run --catalog for discovery, --refresh for
old stock/detail records, or --all for initial setup. Failed pages never retire stock.
"""
import argparse,concurrent.futures,gzip,hashlib,html,json,logging,os,re,time,threading
from pathlib import Path
from urllib.parse import urljoin,urlsplit,quote
from urllib.request import Request,urlopen
from bs4 import BeautifulSoup
import app
HEADERS={'User-Agent':'Mozilla/5.0','Accept':'*/*'}
NIKE='https://api.nike.com/discover/product_wall/v1/marketplace/TR/language/tr/consumerChannelId/d9a5bc42-4b9c-4976-858a-f159cf99c647?path=/tr/w/ayakkabilar-y7ok&attributeIds=16633190-45e5-4830-a068-232ac7aea82c&queryType=PRODUCTS&anchor=0&count=24'
BRANDS=['Nike','Adidas','ECCO','Camper','Puma','New Balance']
DATA=Path(app.DB).parent
CACHE=DATA/'source';MEDIA=DATA/'media'
locks={};last={};guard=threading.Lock()
def fetch(url,json_data=False):
 host=urlsplit(url).netloc
 with guard:lock=locks.setdefault(host,threading.Lock())
 with lock:
  time.sleep(max(0,.3-(time.monotonic()-last.get(host,0))));last[host]=time.monotonic()
  headers=dict(HEADERS,Referer='https://'+host+'/')
  if host=='api.nike.com':headers.update({'nike-api-caller-id':'nike:dotcom:browse:wall.client:2.0','Origin':'https://www.nike.com','Referer':'https://www.nike.com/'})
  for attempt in range(3):
   try:
    with urlopen(Request(url,headers=headers),timeout=25) as r:
     if r.status!=200:raise ValueError('Empty or non-200 response: '+str(r.status))
     data=r.read().decode('utf-8')
     if not data:raise ValueError('Empty response')
     return json.loads(data) if json_data else data
   except Exception as e:
    if getattr(e,'code',None) in (403,404):raise
    if attempt==2:raise
    time.sleep(2*(attempt+1))
def json_scripts(text):
 for script in BeautifulSoup(text,'html.parser').find_all('script'):
  try:yield json.loads(script.string or script.get_text())
  except (ValueError,TypeError):pass
def next_props(text):
 for obj in json_scripts(text):
  if isinstance(obj,dict) and 'props' in obj:return obj['props']['pageProps']
 raise ValueError('Missing page data')
def text(value):return BeautifulSoup(str(value or ''),'html.parser').get_text(' ',strip=True)
def product(brand,sku,name,price,image,url,**extra):
 return {'id':brand.lower().replace(' ','')+'-'+str(sku),'sku':str(sku),'brand':brand,'name':name,'try':float(price),'image':image,'url':url,'category':extra.pop('category',''),'sizes':[],'availability':'unknown','estimated_weight_g':1000,**extra}
def schema_products(source,brand):
 out=[]
 for obj in json_scripts(source):
  for p in app.walk(obj):
   if not isinstance(p,dict) or p.get('@type')!='Product':continue
   o=p.get('offers',{});o=o[0] if isinstance(o,list) and o else o
   if not isinstance(o,dict) or o.get('priceCurrency')!='TRY' or not o.get('price'):continue
   url=p.get('url') or o.get('url','');img=p.get('image','');img=img[0] if isinstance(img,list) and img else img
   sku=p.get('sku') or url.rstrip('/').split('/')[-1]
   out.append(product(brand,sku,p.get('name',''),o['price'],img,url,description=text(p.get('description','')),color=p.get('color',''),availability='in_stock' if str(o.get('availability')).endswith('/InStock') else 'out_of_stock' if str(o.get('availability')).endswith('/OutOfStock') else 'unknown'))
 return out
def nike_page(data):
 out=[]
 for p in app.walk(data):
  if isinstance(p,dict) and p.get('productType')=='FOOTWEAR' and p.get('prices',{}).get('currency')=='TRY' and 'copy' in p:
   out.append(product('Nike',p['productCode'],p['copy']['title'],p['prices']['currentPrice'],p.get('colorwayImages',{}).get('squarishURL',''),p['pdpUrl']['url'],category=p['copy'].get('subTitle',''),color=p.get('displayColors',{}).get('colorDescription',''),merch_id=p.get('merchProductId')))
 return out
def nike_catalog():
 url=NIKE;visited=set();total=None
 while url:
  if url in visited:raise ValueError('Nike pagination repeated')
  visited.add(url);data=fetch(url,True);items=nike_page(data)
  if not items:raise ValueError('Empty Nike catalog page')
  yield items
  pages=data['pages'];total=pages.get('totalResources');n=pages.get('next');url=urljoin('https://api.nike.com',n) if n else None
  if url and urlsplit(url).hostname!='api.nike.com':raise ValueError('Unexpected pagination host')
def adidas_catalog():
 start=0
 while True:
  d=fetch('https://www.adidas.com.tr/api/search/taxonomy?sitePath=tr&query=ayakkabi&start='+str(start),True)['itemList'];items=[]
  if int(d['startIndex'])!=start:raise ValueError('Adidas pagination ignored')
  for p in d['items']:
   if p.get('category')!='shoes':continue
   images=[i['src'] for i in p.get('images',[]) if i.get('src')]
   items.append(product('Adidas',p['productId'],p['displayName'],p.get('salePrice') or p['price'],p.get('image',{}).get('src',images[0] if images else ''),urljoin('https://www.adidas.com.tr',p['link']),category=p.get('subTitle',''),images=images,availability='unknown' if p.get('orderable')==1 else 'out_of_stock',description=p.get('altText',''),reported_weight_g=p.get('weight')))
  if not items:raise ValueError('Empty Adidas page')
  yield items;start+=int(d['viewSize'])
  if start>=int(d['count']):break
def camper_item(p):
 sizes=[{'label':str(s.get('label') or s.get('value')),'available':bool(s.get('available')),'price_try':s.get('price',{}).get('current')} for s in (p.get('sizes') or []) if s.get('label') or s.get('value')]
 sources=(p.get('imageSources') or {}).get('sources',[]);images=next((s['links'] for s in sources if s['type']=='lg'),sources[0]['links'] if sources else [])
 images=[s['url'] for s in images if not s.get('video')]
 return product('Camper',p['id'],p['name'],p['prices']['current'],images[0] if images else p.get('imageBag',''),p['url'],category=p.get('tipology',''),description=p.get('subtitleH1',''),color=p.get('color',''),images=images,sizes=sizes,size_system='EU',availability='in_stock' if any(s['available'] for s in sizes) else 'out_of_stock',stock_checked=app.now())
def camper_catalog():
 for gender in ['men','women','kids']:
  page=1
  while True:
   d=next_props(fetch('https://www.camper.com/en_TR/'+gender+'/shoes?page='+str(page)))
   if int(d.get('initialPage',page))!=page:raise ValueError('Camper pagination ignored')
   products=d.get('firstProducts',[])
   if not products:raise ValueError('Empty Camper page')
   yield [camper_item(p) for p in products if p.get('prices',{}).get('current')]
   if page>=int(d.get('totalPages',1)):break
   page+=1
def nb_item(p):
 sizes=[]
 for b in p.get('barcodes',[]):
  label=' / '.join(v['name'] for v in b.get('stockTypeValues',[]))
  if label:sizes.append({'label':label,'available':float(p.get('stocksByBarcode',{}).get(str(b['id']),0))>0})
 images=['https://newbalance.sm.mncdn.com/mnresize/640/-/'+d['filePath'] for d in p.get('documents',[]) if d.get('documentType')=='Image']
 return product('New Balance',p['sku'],p['name'],p.get('discountPrice') or p['salesPrice'],images[0] if images else '',urljoin('https://www.newbalance.com.tr/urun/',p['routePath']),images=images,category=' / '.join(c['name'] for c in p.get('categories',[]) if c.get('isDefault')),sizes=sizes,size_system='EU',availability='in_stock' if any(s['available'] for s in sizes) else 'out_of_stock',stock_checked=app.now())
def nb_catalog():
 for category in ['erkek-spor-ayakkabi','kadin-spor-ayakkabi','cocuk-ayakkabi']:
  page=1
  while True:
   d=next_props(fetch('https://www.newbalance.com.tr/'+category+'?page='+str(page)))['data']['response']
   if int(d['currentPage'])!=page:raise ValueError('New Balance pagination ignored')
   if not d.get('products'):raise ValueError('Empty New Balance page')
   yield [nb_item(p) for p in d['products']]
   if page>=int(d['totalPages']):break
   page+=1
def puma_catalog():
 for gender in ['erkek','kadin','cocuk/erkek-cocuk','cocuk/kiz-cocuk']:
  page=1;previous=None
  while True:
   s=fetch('https://tr.puma.com/'+(gender+'/ayakkabi.html')+'?p='+str(page));items=schema_products(s,'Puma')
   if not items:raise ValueError('Empty Puma page')
   fingerprint=tuple(p['id'] for p in items)
   if fingerprint==previous:raise ValueError('Puma pagination repeated')
   previous=fingerprint;yield items
   soup=BeautifulSoup(s,'html.parser');next_link=soup.select_one('a.action.next, a[rel=next], a.js-load-more')
   if not next_link:break
   page+=1
   if page>200:raise ValueError('Puma page limit')
def ecco_tiles(s):
 soup=BeautifulSoup(s,'html.parser');out=[]
 for tile in soup.select('.product-tile'):
  a=tile.select_one('a.product-name');img=tile.select_one('img.swipe-img');prices=tile.select('.c-price .value')
  if not a or not img or not prices:continue
  url=urljoin('https://tr.ecco.com',a['href']);price=float(re.sub('[^0-9.]','',prices[-1].get_text()))
  out.append(product('ECCO',url.split('_')[-1],a.get_text(strip=True),price,img.get('data-src') or img.get('src'),url,category='Shoes'))
 return out
def ecco_catalog():
 # All shoe category pages plus their published XML product sitemap.
 urls=[]
 sitemap=fetch('https://tr.ecco.com/sitemap.xml')
 for u in re.findall(r'<loc>(.*?)</loc>',sitemap):
  if 'sitemap-products_en' in u.lower():
   body=fetch(html.unescape(u))
   urls.extend(u for u in re.findall(r'<loc>(.*?)</loc>',body) if '/en/' in u)
 if urls:
  app.save('ecco_discovered',list(dict.fromkeys(urls)))
 for gender in ['men','women']:
  yield ecco_tiles(fetch('https://tr.ecco.com/en/'+gender+'/shoes'))
 # Site has infinite scroll without crawlable next links. Sitemap details are
 # processed by the enrichment queue, ensuring additional products are discovered.
 for u in dict.fromkeys(urls):
  yield [{'id':'ecco-'+u.split('_')[-1],'brand':'ECCO','url':u,'pending_discovery':True}]
CATALOGS={'Nike':nike_catalog,'Adidas':adidas_catalog,'Camper':camper_catalog,'New Balance':nb_catalog,'Puma':puma_catalog,'ECCO':ecco_catalog}
def upsert(p):
 if p.get('pending_discovery'):
  with app.db() as c:
   if not c.execute('SELECT 1 FROM products WHERE id=?',(p['id'],)).fetchone():c.execute('INSERT OR IGNORE INTO discovery VALUES (?,?)',(p['id'],p['url']))
  return
 if not p.get('url') or not p.get('try') or p['try']<=0:return
 stamp=app.now()
 with app.db() as c:
  old=c.execute('SELECT data FROM products WHERE id=?',(p['id'],)).fetchone();previous=json.loads(old[0]) if old else {}
  incoming_stock=bool(p.get('stock_checked'))
  # Source text is captured once. Fresh feeds replace prices, sizes and availability.
  for key in ['description','features','images','content_fetched','detail_checked','detail_error','stock_checked','size_system','local_image','inventory_error']:
   if previous.get(key) and (key not in p or not p[key] or key in ['description','features','content_fetched']):p[key]=previous[key]
  if not incoming_stock and not p.get('sizes') and previous.get('sizes'):p['sizes']=previous['sizes']
  # A style appearing in a listing does not prove that any size has restocked.
  if not incoming_stock and previous.get('availability')=='out_of_stock':p['availability']='out_of_stock'
  if 'stock_checked' not in p and previous.get('stock_checked'):p['stock_checked']=previous['stock_checked']
  p['listed']=True;p['catalog_checked']=stamp
  c.execute('INSERT OR REPLACE INTO products VALUES (?,?,?,?)',(p['id'],p['brand'],json.dumps(p,ensure_ascii=False),stamp))
def init():
 app.init();CACHE.mkdir(parents=True,exist_ok=True);MEDIA.mkdir(parents=True,exist_ok=True)
 with app.db() as c:c.execute('CREATE TABLE IF NOT EXISTS discovery(id TEXT PRIMARY KEY,url TEXT)')
def catalog_brand(brand):
 status=app.state('sync',{}).get(brand,{})
 seen=set();pages=0
 app.save('progress:'+brand,{'running':True,'started':app.now(),'pages':0,'count':0})
 try:
  for batch in CATALOGS[brand]():
   for p in batch:upsert(p);seen.add(p['id'])
   pages+=1;app.save('progress:'+brand,{'running':True,'pages':pages,'count':len(seen),'at':app.now()})
   logging.info('%s page %s: %s products',brand,pages,len(seen))
  if not seen:raise ValueError('No products discovered')
  with app.db() as c:
   for row in c.execute('SELECT id,data FROM products WHERE brand=?',(brand,)).fetchall():
    if row['id'] not in seen and brand!='ECCO':
     p=json.loads(row['data']);p['listed']=False;p['availability']='out_of_stock'
     c.execute('UPDATE products SET data=? WHERE id=?',(json.dumps(p,ensure_ascii=False),row['id']))
  status={'ok':True,'at':app.now(),'count':len(seen),'pages':pages,'scope':'Published sitemap discovered; details queued' if brand=='ECCO' else 'All published catalog pages'}
 except Exception as e:
  logging.exception('%s catalog failed',brand);status={'ok':False,'at':app.now(),'count':len(seen),'pages':pages,'error':str(e),'previous_success':status.get('at')}
 app.save('brand:'+brand,status);app.save('progress:'+brand,dict(status,running=False))
 return brand,status
def nike_details(source,p):
 d=next_props(source)['selectedProduct']
 if d['styleColor']!=p['sku']:raise ValueError('Source returned a different colorway')
 info=d['productInfo'];sizes=[{'label':str(s.get('localizedLabel') or s['label']),'available':None,'active':s.get('status')=='ACTIVE','sku':s.get('merchSkuId')} for s in d.get('sizes',[])]
 # ACTIVE is a catalog status, not a stock count. Do not label it in-stock.
 return {'merch_id':d.get('merchProductId'),'description':info.get('productDescription',''),'features':[text(v) for section in info.get('featuresAndBenefits',[])+info.get('productDetails',[]) for v in section.get('body',[])],'images':[i['properties']['squarish']['url'] for i in d.get('contentImages',[]) if i.get('properties',{}).get('squarish',{}).get('url')],'sizes':sizes,'size_system':d.get('localizedLabelPrefix','EU'),'availability':'out_of_stock' if d.get('statusModifier') in ['SOLD_OUT','NOT_BUYABLE'] else 'unknown','try':d['prices']['currentPrice']}
def puma_details(source,p):
 out={};soup=BeautifulSoup(source,'html.parser')
 schemas=schema_products(source,'Puma')
 if schemas:out.update({k:schemas[0][k] for k in ['description','availability','color'] if schemas[0].get(k)})
 for obj in json_scripts(source):
  for o in app.walk(obj):
   if not isinstance(o,dict) or 'spConfig' not in o:continue
   cfg=o['spConfig'];attrs=cfg.get('attributes',{});sizes=[]
   code,color=p['sku'].replace('_','-').rsplit('-',1)
   selected={pid for pid,imgs in cfg.get('images',{}).items() if any('/'+code+'/'+color+'/' in i.get('img','') for i in imgs)}
   if not selected:continue
   for a in attrs.values():
    if a.get('code') not in ['size','puma_size','beden']:continue
    for opt in a.get('options',[]):sizes.append({'label':str(opt.get('pdp_size') or opt['label']),'available':bool(selected.intersection(opt.get('products',[])))})
   if sizes:out.update(sizes=sizes,size_system='EU',availability='in_stock' if any(s['available'] for s in sizes) else 'out_of_stock')
 desc=soup.select_one('.product.attribute.description, #description, .product-info__description')
 if desc:out['description']=desc.get_text(' ',strip=True)
 return out
def ecco_details(source,p):
 soup=BeautifulSoup(source,'html.parser');out={}
 schemas=schema_products(source,'ECCO')
 if schemas:out.update(schemas[0])
 for obj in json_scripts(source):
  for v in app.walk(obj):
   if isinstance(v,dict) and v.get('@type')=='ProductGroup':
    variants=v.get('hasVariant',[])
    if variants:
     out['sizes']=[{'label':str(x['size']),'available':str(x.get('offers',{}).get('availability','')).endswith('/InStock')} for x in variants if x.get('size')]
     out['images']=variants[0].get('image',[])
     if out['images']:out['image']=out['images'][0]
     out['size_system']='EU';out['availability']='in_stock' if any(x['available'] for x in out['sizes']) else 'out_of_stock'
 name=soup.select_one('h1');price=soup.select_one('.price-sales .value, .price-standard .value')
 if name:out['name']=name.get_text(' ',strip=True)
 if price:out['try']=float(re.sub('[^0-9.]','',price.get_text()))
 sizes=out.get('sizes',[])
 for e in soup.select('[data-size], [data-sizeid], .size-list a, .size-list li, .bedenList li'):
  label=e.get('data-size') or e.get_text(' ',strip=True)
  if re.fullmatch(r'\d{1,2}(?:[.,]\d)?',label):sizes.append({'label':label,'available':not any(t in ' '.join(e.get('class',[])).lower() for t in ['disabled','outofstock','passive','nostock'])})
 if sizes:out.update(sizes=sizes,size_system='EU',availability='in_stock' if any(s['available'] for s in sizes) else 'out_of_stock')
 desc=soup.select_one('.product-description, #product-description, .product-details-description, .product-detail-description, .pdp-more-details .description-title')
 if desc:out['description']=desc.get_text(' ',strip=True)
 imgs=[i.get('data-src') or i.get('src') for i in soup.select('.product-detail img, .product-images img, .product-image img')];imgs=[u for u in imgs if u and 'ContentImages/Product/' in u]
 if imgs:out['images']=list(dict.fromkeys(imgs));out['image']=imgs[0]
 return out
def details(p):
 # Retrieval is scheduled, never triggered by a storefront request.
 try:
  brand=p['brand'];out={}
  if brand=='Adidas':
   stock=fetch('https://www.adidas.com.tr/api/products/'+p['sku']+'/availability?sitePath=tr',True)
   source=json.dumps(stock)
  else:source=fetch(p['url'])
  if brand=='Nike':
   out=nike_details(source,p)
   try:
    inventory=fetch('https://api.nike.com/deliver/available_skus/v1?filter=productIds('+str(out.get('merch_id') or p.get('merch_id'))+')',True)
    stock={v['skuId']:v['available'] for v in inventory.get('objects',[]) if v.get('skuId') and isinstance(v.get('available'),bool)}
    for size in out['sizes']:size['available']=stock.get(size.get('sku'))
    if any(s['available'] is True for s in out['sizes']):out['availability']='in_stock'
    elif out['sizes'] and all(s['available'] is False for s in out['sizes']):out['availability']='out_of_stock'
    out['inventory_error']=None
   except Exception as error:out['inventory_error']=str(error)
  elif brand=='Puma':out=puma_details(source,p)
  elif brand=='ECCO':out=ecco_details(source,p)
  elif brand=='Camper':
   props=next_props(source)
   for v in app.walk(props):
    if isinstance(v,dict) and v.get('id')==p.get('sku') and v.get('sizes') and v.get('prices'):out=camper_item(v);break
   if not out:
    schemas=schema_products(source,brand)
    if schemas:out={k:schemas[0][k] for k in ['description']}
  elif brand=='New Balance':
   props=next_props(source)
   for v in app.walk(props):
    if isinstance(v,dict) and v.get('sku')==p['sku'] and 'barcodes' in v:out=nb_item(v);out['description']=text(v.get('description') or v.get('routeDescription'));break
  elif brand=='Adidas':
   # Stock API may be unavailable even when the catalog works. Retry on schedule.
   d=stock
   sizes=[{'label':str(s['size']),'available':s.get('availability',0)>0} for s in d.get('variation_list',[])]
   if 'variation_list' not in d:raise ValueError('Missing Adidas size stock data')
   out.update(sizes=sizes,size_system='EU',availability='in_stock' if any(s['available'] for s in sizes) else 'out_of_stock')
  stamp=app.now()
  with app.db() as c:
   row=c.execute('SELECT data FROM products WHERE id=?',(p['id'],)).fetchone()
   if not row:return
   current=json.loads(row[0])
   for k,v in out.items():
    if k in ['id','url','brand']:continue
    if k in ['description','features','images'] and current.get('content_fetched') and current.get(k):continue
    if (v is not None and v!=[] and v!='') or k in ['inventory_error','sizes']:current[k]=v
   current['detail_checked']=stamp;current['detail_error']=None
   if 'sizes' in out:current['stock_checked']=stamp
   if not current.get('content_fetched'):
    current['content_fetched']=stamp;gzip.open(CACHE/(p['id']+'.html.gz'),'wt').write(source)
   c.execute('UPDATE products SET data=? WHERE id=?',(json.dumps(current,ensure_ascii=False),p['id']))
  return True
 except Exception as e:
  with app.db() as c:
   row=c.execute('SELECT data FROM products WHERE id=?',(p['id'],)).fetchone()
   if row:
    current=json.loads(row[0]);current['detail_attempt']=app.now();current['detail_error']=str(e);c.execute('UPDATE products SET data=? WHERE id=?',(json.dumps(current,ensure_ascii=False),p['id']))
  return False
def image(p):
 try:
  target=MEDIA/(p['id']+'.img')
  if not target.exists():
   u=p.get('image','')
   if not u.startswith('https://'):return
   u=u.replace('w_280,h_280','w_500,h_500').replace('t_default','t_PDP_864_v1') if p['brand']=='Adidas' else u
   with urlopen(Request(u,headers=HEADERS),timeout=20) as r:
    if not r.headers.get('Content-Type','').startswith('image/'):return
    data=r.read(3000000)
    if len(data)>=3000000:return
    target.write_bytes(data)
  with app.db() as c:
   row=c.execute('SELECT data FROM products WHERE id=?',(p['id'],)).fetchone()
   if row:
    current=json.loads(row[0]);current['local_image']='/media/'+p['id']+'.img';c.execute('UPDATE products SET data=? WHERE id=?',(json.dumps(current,ensure_ascii=False),p['id']))
 except Exception:pass
def discover_ecco(limit):
 with app.db() as c:pending=c.execute('SELECT * FROM discovery LIMIT ?',(limit,)).fetchall()
 for row in pending:
  try:
   with app.db() as c:existing=c.execute('SELECT data FROM products WHERE id=?',(row['id'],)).fetchone()
   if existing and json.loads(existing[0]).get('content_fetched'):
    with app.db() as c:c.execute('DELETE FROM discovery WHERE id=?',(row['id'],))
    continue
   source=fetch(row['url']);out=ecco_details(source,{'url':row['url']})
   name=out.get('name','')
   if out.get('try') and not any(w in name.lower() for w in ['sock','lace','insole','foam cleaner','shoe care','shoe horn','shoe shine','brush','spray','bag','belt','wallet','umbrella','glove','cap','bottle']):
    p=product('ECCO',row['id'][5:],name,out['try'],out.get('image',''),row['url'],**{k:v for k,v in out.items() if k not in ['id','brand','sku','url','name','try','image']})
    p['stock_checked']=app.now();p['detail_checked']=app.now();p['content_fetched']=app.now();upsert(p);gzip.open(CACHE/(p['id']+'.html.gz'),'wt').write(source)
   with app.db() as c:c.execute('DELETE FROM discovery WHERE id=?',(row['id'],))
  except Exception as e:logging.warning('ECCO discovery %s: %s',row['id'],e)
def refresh(limit):
 discover_ecco(max(20,limit//3))
 with app.db() as c:rows=c.execute('SELECT data FROM products').fetchall()
 products=[json.loads(r[0]) for r in rows];products=[p for p in products if p.get('listed',True)]
 def priority(p):
  # Failed upstream endpoints retry at most once per six hours.
  at=p.get('detail_attempt') if p.get('detail_error') else p.get('detail_checked')
  if at and (app.datetime.now(app.timezone.utc)-app.datetime.fromisoformat(at)).total_seconds()<21600:return None
  return (bool(p.get('content_fetched')),at or '',p['id'])
 due=[(priority(p),p) for p in products if priority(p) is not None];due.sort(key=lambda v:v[0])
 buckets={b:[] for b in BRANDS}
 for _,p in due:buckets[p['brand']].append(p)
 due=[]
 while len(due)<limit and any(buckets.values()):
  for b in BRANDS:
   if buckets[b] and len(due)<limit:due.append(buckets[b].pop(0))
 # Use small batches so one slow upstream doesn't block the queue indefinitely.
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:results=list(ex.map(details,due))
 image_buckets={b:[p for p in products if p['brand']==b and not p.get('local_image')] for b in BRANDS}
 images=[]
 while len(images)<limit*2 and any(image_buckets.values()):
  for b in BRANDS:
   if image_buckets[b] and len(images)<limit*2:images.append(image_buckets[b].pop(0))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:list(ex.map(image,images))
 app.save('enrichment',{'at':app.now(),'attempted':len(due),'succeeded':sum(results),'remaining':max(0,len(products)-sum(bool(p.get('content_fetched')) for p in products))})
 logging.info('Refreshed %d details; %d succeeded',len(due),sum(results))
def run():
 parser=argparse.ArgumentParser();parser.add_argument('--catalog',action='store_true');parser.add_argument('--refresh',action='store_true');parser.add_argument('--all',action='store_true');parser.add_argument('--brand',choices=BRANDS);parser.add_argument('--limit',type=int,default=120);args=parser.parse_args();init()
 import fcntl
 with open(DATA/'import.lock','w') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:logging.info('Another import is running');return
  if args.catalog or args.all:
   app.sync_prices()
   brands=[args.brand] if args.brand else BRANDS
   with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:results=list(ex.map(catalog_brand,brands))
   status=app.state('sync',{})
   for brand,result in results:status[brand]=result
   app.save('sync',status)
  if args.refresh or args.all:refresh(args.limit)
if __name__=='__main__':
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s');run()
