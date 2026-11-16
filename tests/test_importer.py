import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import app,importer
F=Path(__file__).parent/'fixtures'
def fixture(name):return json.loads((F/(name+'.json')).read_text())
def script(name):return '<script type="application/json">'+json.dumps(fixture(name))+'</script>'
class ImportTests(unittest.TestCase):
 def test_nike_sizes_do_not_claim_active_means_stock(self):
  p=fixture('nike')['props']['pageProps']['selectedProduct'];d=importer.nike_details(script('nike'),{'sku':p['styleColor']})
  self.assertTrue(d['sizes']);self.assertEqual(d['sizes'][0]['label'],'35.5');self.assertIsNone(d['sizes'][0]['available']);self.assertTrue(d['description'])
 def test_nike_rejects_wrong_color(self):
  with self.assertRaises(ValueError):importer.nike_details(script('nike'),{'sku':'wrong-color'})
 def test_camper_has_size_stock_and_price(self):
  p=importer.camper_item(fixture('camper'));self.assertEqual(p['sizes'][0]['label'],'39');self.assertTrue(p['sizes'][0]['available']);self.assertEqual(p['try'],10999)
 def test_nb_out_of_stock_size_preserved(self):
  p=importer.nb_item(fixture('newbalance'));sizes={s['label']:s['available'] for s in p['sizes']};self.assertFalse(sizes['45']);self.assertTrue(sizes['44.5'])
 def test_puma_stock_is_specific_to_selected_color(self):
  p=importer.puma_details(script('puma'),{'sku':'407168_02'});sizes={s['label']:s['available'] for s in p['sizes']}
  self.assertFalse(sizes['36']);self.assertTrue(sizes['37']);self.assertFalse(sizes['48'])
 def test_ecco_schema_variants(self):
  p=importer.ecco_details(script('ecco'),{});self.assertEqual(p['sizes'][0]['label'],'40');self.assertTrue(p['sizes'][0]['available'])
 def test_upsert_preserves_cached_text_refreshes_stock(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',tmp+'/shop.db'):
   app.init();p=importer.product('Camper','A','Name',100,'https://image.test/a','https://www.camper.com/a',description='Original content',content_fetched=app.now(),sizes=[{'label':'42','available':True}]);importer.upsert(p)
   p=importer.product('Camper','A','Name',120,'https://image.test/a','https://www.camper.com/a',description='Changed content',sizes=[{'label':'42','available':False}]);importer.upsert(p)
   with app.db() as c:d=json.loads(c.execute('SELECT data FROM products').fetchone()[0])
   self.assertEqual(d['description'],'Original content');self.assertEqual(d['try'],120);self.assertFalse(d['sizes'][0]['available'])

class StockRefreshTests(unittest.TestCase):
 def test_catalog_listing_cannot_reactivate_sold_out_product(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',tmp+'/shop.db'):
   app.init()
   p=importer.product('Adidas','IH5422','Breaknet',1739,'https://image.test/a','https://www.adidas.com.tr/a',availability='out_of_stock');importer.upsert(p)
   p=importer.product('Adidas','IH5422','Breaknet',1739,'https://image.test/a','https://www.adidas.com.tr/a',availability='in_stock');importer.upsert(p)
   with app.db() as c:d=json.loads(c.execute('SELECT data FROM products').fetchone()[0])
   self.assertEqual(d['availability'],'out_of_stock');self.assertFalse(app.orderable(d))
   p.update(sizes=[{'label':'42','available':True}],stock_checked=app.now(),availability='in_stock');importer.upsert(p)
   with app.db() as c:d=json.loads(c.execute('SELECT data FROM products').fetchone()[0])
   self.assertTrue(app.orderable(d))
 def test_authoritative_empty_stock_clears_previous_sizes(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',tmp+'/shop.db'):
   app.init();p=importer.product('Camper','A','Test',100,'https://image.test/a','https://www.camper.com/a',availability='in_stock',sizes=[{'label':'42','available':True}],stock_checked=app.now());importer.upsert(p)
   p.update(sizes=[],availability='out_of_stock',stock_checked=app.now());importer.upsert(p)
   with app.db() as c:d=json.loads(c.execute('SELECT data FROM products').fetchone()[0])
   self.assertEqual(d['sizes'],[]);self.assertFalse(app.orderable(d))
