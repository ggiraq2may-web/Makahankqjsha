"""Offline tests only; no live Midasbuy connection or mutations."""
import json
import unittest
from pathlib import Path

from region_lab import evaluate

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def load(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


class RegionLabTests(unittest.TestCase):
    def test_session_only_discrepancy(self):
        result = evaluate(load("session_specific.json"))
        self.assertEqual(result["classification"], "SESSION_SPECIFIC_RESULT")
        self.assertIn("SESSION_DEPENDENT_RESULT_OBSERVED", result["evidence"])
        self.assertFalse(result["turkish_purchase_eligibility_verified"])

    def test_cross_session_still_not_server_proof(self):
        result = evaluate(load("cross_session_display.json"))
        self.assertEqual(result["classification"], "CROSS_SESSION_DISPLAY_OBSERVED")
        self.assertFalse(result["user_region_mutation_verified"])
        self.assertFalse(result["turkish_purchase_eligibility_verified"])

    def test_only_one_session_is_not_a_verification(self):
        data = load("session_specific.json")
        data["observations"] = data["observations"][:2]
        result = evaluate(data)
        self.assertEqual(result["classification"], "SESSION_ONLY_CHANGE_OBSERVED")
        self.assertIn("لا توجد نتيجة من جلسة جديدة مستقلة بعد العملية.", result["limitations"])

    def test_turkey_label_other_currency(self):
        data = {"sku":"DEMO", "observations": [
            {"phase":"before","session":"A","independently_started":False,"shop_country":"AE","currency":"USD"},
            {"phase":"after","session":"A","independently_started":False,"shop_country":"TR","currency":"USD"},
        ]}
        result = evaluate(data)
        self.assertIn("TURKEY_LABEL_WITH_NON_TRY_CURRENCY", result["evidence"])

    def test_invalid_evidence(self):
        with self.assertRaises(ValueError):
            evaluate({"sku": "UC60", "observations": [{"phase": "after", "session": "A",
                "independently_started": False, "shop_country": "TR", "currency": "TRY", "price": "NaN"}]})

    def test_never_reports_purchase_eligibility_as_verified(self):
        data = load("cross_session_display.json")
        for record in data["observations"]:
            record["eligibility"] = "allowed"  # user observed, not authoritative evidence
        result = evaluate(data)
        self.assertNotIn("account_evidence", result)
        self.assertFalse(result["turkish_purchase_eligibility_verified"])

    def test_account_readbacks_and_comparable_checkout_totals(self):
        data = {
            "sku": "DEMO_UC60",
            "account_evidence": {
                "before": {
                    "region": {"user_region": "AE", "reference": "region_before"},
                    "checkout": {"shop_country": "TR", "outcome": "quoted",
                                 "currency": "TRY", "total": "59.90",
                                 "payment_method": "card", "reference": "quote_before"},
                },
                "after": {
                    "region": {"user_region": "TR", "reference": "region_after"},
                    "checkout": {"shop_country": "TR", "outcome": "quoted",
                                 "currency": "TRY", "total": "49.90",
                                 "payment_method": "card", "reference": "quote_after"},
                },
            },
        }
        result = evaluate(data)
        account = result["account_evidence"]
        self.assertTrue(account["user_region_changed_as_reported"])
        self.assertTrue(account["comparable_checkout_total_changed_as_reported"])
        self.assertEqual(account["turkish_checkout_outcome_as_reported"], "quoted")
        self.assertFalse(result["user_region_mutation_verified"])
        self.assertFalse(result["turkish_purchase_eligibility_verified"])
        self.assertFalse(result["operation_performed"])

        after = data["account_evidence"]["after"]["checkout"]
        for key, value in (("currency", "USD"), ("payment_method", "wallet"), ("outcome", "paid")):
            with self.subTest(key=key):
                original = after[key]
                after[key] = value
                account = evaluate(data)["account_evidence"]
                self.assertIsNone(account["comparable_checkout_total_changed_as_reported"])
                after[key] = original

    def test_rejected_checkout_is_distinct_from_no_checkout_or_payment(self):
        checkout = {"shop_country": "TR", "outcome": "rejected",
                    "payment_method": "card", "reference": "rejection"}
        data = {"sku": "DEMO_UC60", "account_evidence": {"after": {"checkout": checkout}}}
        result = evaluate(data)["account_evidence"]
        self.assertEqual(result["turkish_checkout_outcome_as_reported"], "rejected")
        self.assertIsNone(result["comparable_checkout_total_changed_as_reported"])
        self.assertIsNone(result["user_region_changed_as_reported"])
        checkout["shop_country"] = "AE"
        self.assertIsNone(evaluate(data)["account_evidence"]["turkish_checkout_outcome_as_reported"])
        checkout["total"] = "25.00"
        with self.assertRaises(ValueError):
            evaluate(data)


if __name__ == "__main__":
    unittest.main()

class EvidenceEdgeCases(unittest.TestCase):
    def test_one_after_session_not_repeated(self):
        """Regression: a single independent after session was mislabeled repeated."""
        data = {'sku':'DEMO_UC60','observations':[
            {'phase':'before','session':'A','independently_started':False,
             'shop_country':'AE','currency':'USD','price':'0.99'},
            {'phase':'after','session':'B','independently_started':True,
             'shop_country':'TR','currency':'TRY','price':'49.99'}]}
        result = evaluate(data)
        self.assertNotIn('TURKISH_DISPLAY_REPEATED_ACROSS_SESSIONS',result['evidence'])
        self.assertEqual(result['classification'],'INSUFFICIENT_EVIDENCE')
        self.assertFalse(result['turkish_purchase_eligibility_verified'])
        self.assertFalse(result['operation_performed'])

    def test_same_named_session_cannot_look_like_two_sessions(self):
        data = {'sku':'DEMO_UC60','observations':[
            {'phase':'before','session':'A','independently_started':False,'shop_country':'AE','currency':'USD'},
            {'phase':'after','session':'A','independently_started':False,'shop_country':'TR','currency':'TRY'},
            {'phase':'after','session':'A','independently_started':True,'shop_country':'TR','currency':'TRY'}]}
        result = evaluate(data)
        self.assertNotIn('TURKISH_DISPLAY_REPEATED_ACROSS_SESSIONS',result['evidence'])
        self.assertNotEqual(result['classification'],'CROSS_SESSION_DISPLAY_OBSERVED')

    def test_bad_price_and_metadata_rejected(self):
        baseline = {'phase':'after','session':'B','independently_started':True,
                    'shop_country':'TR','currency':'TRY','price':'2.00'}
        for field,bad in [('price','-1'),('price','Infinity'),('session','?'),
                          ('currency','TL'),('shop_country','Turkey'),
                          ('independently_started','yes'),('eligibility','verified')]:
            obs = dict(baseline, **{field:bad})
            with self.subTest(field=field),self.assertRaises(ValueError):
                evaluate({'sku':'DEMO_UC60','observations':[obs]})

    def test_unknown_eligibility_stays_unverified(self):
        for fixture in ('session_specific.json','cross_session_display.json'):
            result=evaluate(load(fixture))
            self.assertIs(result['user_region_mutation_verified'],False)
            self.assertIs(result['turkish_purchase_eligibility_verified'],False)
            self.assertIs(result['operation_performed'],False)
