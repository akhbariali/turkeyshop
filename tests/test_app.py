import unittest,tempfile,os,json
import app
class ShopTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();app.DB=self.tmp.name+'/test.db';app.init()
  app.save('rate',{'fetched':app.now(),'data':{'rate':6190,'updated_at':app.now()}})
  app.save('shipping',{'fetched':app.now(),'data':{'options':[{'id':'air_apparel','rate_per_kg':690000},{'id':'ground_apparel','rate_per_kg':590000}]}})
  with app.db() as c:c.execute('INSERT INTO products VALUES (?,?,?,?)',('shoe','Nike',json.dumps({'id':'shoe','name':'Test','try':1000,'availability':'in_stock','stock_checked':app.now(),'sizes':[{'label':'42','available':True}]}),app.now()))
 def tearDown(self):self.tmp.cleanup()
 def test_quote(self):
  q=app.quote([{'id':'shoe','qty':2,'size':'42'}],grams=1000)
  self.assertEqual(q['goods'],12380000);self.assertEqual(q['commission'],1238000);self.assertEqual(q['shipping'],1380000);self.assertEqual(q['total'],14998000)
 def test_invalid(self):
  for items in [[],[{'id':'fake'}],[{'id':'shoe','qty':-1}]]:
   with self.assertRaises(ValueError):app.quote(items)
 def test_stale(self):
  app.save('rate',{'fetched':'2020-01-01T00:00:00+00:00','data':{'rate':6190}})
  with self.assertRaises(ValueError):app.quote([{'id':'shoe'}])
 def test_parser(self):
  p={'productType':'FOOTWEAR','productCode':'A','prices':{'currency':'TRY','currentPrice':100},'copy':{'title':'Shoe'},'pdpUrl':{'url':'https://www.nike.com/tr/t/shoe'}}
  self.assertEqual(app.parse_products('<script>'+json.dumps({'nested':[p]})+'</script>','Nike')[0]['try'],100)
if __name__=='__main__':unittest.main()

class AvailabilityTests(ShopTests):
 def update_product(self,**extra):
  with app.db() as c:
   p=json.loads(c.execute('SELECT data FROM products WHERE id=?',('shoe',)).fetchone()[0]);p.update(extra);c.execute('UPDATE products SET data=? WHERE id=?',(json.dumps(p),'shoe'))
 def test_sold_out_size_cannot_be_ordered(self):
  self.update_product(sizes=[{'label':'42','available':False}],stock_checked=app.now())
  with self.assertRaisesRegex(ValueError,'out of stock'):app.quote([{'id':'shoe','size':'42'}])
 def test_unlisted_size_cannot_be_ordered(self):
  self.update_product(sizes=[{'label':'42','available':True}],stock_checked=app.now())
  with self.assertRaisesRegex(ValueError,'listed size'):app.quote([{'id':'shoe','size':'99'}])
 def test_size_specific_price(self):
  self.update_product(sizes=[{'label':'42','available':True,'price_try':1100}],stock_checked=app.now())
  self.assertEqual(app.quote([{'id':'shoe','size':'42'}])['goods'],6809000)
 def test_card_estimate_includes_shipping(self):
  estimate=app.estimate({'try':1000});self.assertEqual(estimate['total'],7499000);self.assertEqual(estimate['shipping'],690000)

class StrictStockTests(AvailabilityTests):
 def test_missing_size_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'available size'):app.quote([{'id':'shoe'}])
 def test_unknown_size_stock_is_rejected(self):
  self.update_product(sizes=[{'label':'42','available':None}])
  with self.assertRaisesRegex(ValueError,'unverified'):app.quote([{'id':'shoe','size':'42'}])
 def test_empty_sizes_cannot_use_free_text(self):
  self.update_product(sizes=[],availability='in_stock')
  with self.assertRaisesRegex(ValueError,'listed size'):app.quote([{'id':'shoe','size':'42'}])
 def test_stale_stock_is_rejected_even_if_previously_available(self):
  self.update_product(stock_checked='2020-01-01T00:00:00+00:00')
  with self.assertRaisesRegex(ValueError,'stale'):app.quote([{'id':'shoe','size':'42'}])
 def test_sold_out_product_cannot_use_previous_sizes(self):
  self.update_product(availability='out_of_stock')
  with self.assertRaisesRegex(ValueError,'out of stock'):app.quote([{'id':'shoe','size':'42'}])
 def test_orderability_matches_checkout(self):
  self.assertFalse(app.orderable({'sizes':[],'stock_checked':app.now(),'availability':'in_stock'}))
  self.assertFalse(app.orderable({'sizes':[{'available':None}],'stock_checked':app.now()}))
  self.assertTrue(app.orderable({'sizes':[{'available':True}],'stock_checked':app.now()}))
