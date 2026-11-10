import unittest
from workbench.domain import *

class DomainBoundaryTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(balance(8,2,2,2),2)
        with self.assertRaises(ValueError):balance(8,-2,2,2)
        with self.assertRaises(ValueError):overused([{'receipt':'x','qty':-1},{'receipt':'x','qty':2}],{'x':2})
        g={'signer':'a','site':'东','start':'2026-01-01','end':'2026-01-02'}
        self.assertTrue(authorized(g,'a','东','2026-01-02'))
        self.assertFalse(authorized(g,'a','东','2026-01-03'))
