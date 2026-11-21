# Kro GeoIP — Midasbuy Turkish shop edition

**Purpose:** Telegram player lookup and **access to the official Turkish PUBG Mobile Midasbuy storefront**, with truthful status information. This is **not** a server-side account region changer, checkout API, or payment eligibility bypass.

## UI

- `/start` — GeoIP / official Turkish shop / purchase eligibility info.
- `GeoIP` — provide Player ID, get lookup data if an authorized provider is configured. Player name and region may remain unverified.
- `متجر تركيا الرسمي` — open the official `https://www.midasbuy.com/midasbuy/tr/buy/pubgm` page.
- `/shop` — official Turkish shop link and terms explanation.
- `/history` — recent lookups and operation/verification status.
- `/cancel` — cancel Player ID input.

**Important:** Visiting a Turkish storefront does not prove that a user is allowed to buy there. Midasbuy might enforce account-region and payment restrictions. Kro does not override them or claim to have changed `shop_region`, `user_region` or `payment_region`.

## Railway worker

1. Import this repository in Railway and set the service **Root Directory** to `KroGeoIP`.
   The included `railway.toml` and `Procfile` start `python main.py`; no web port is required.
2. Add the following Railway variables (keep secrets in Railway, not Git):

   ```dotenv
   BOT_TOKEN=YOUR_BOT_TOKEN
   DB_PATH=/data/kro_geoip.db
   PLAYER_LOOKUP_URL=
   PLAYER_LOOKUP_TOKEN=
   REQUEST_TIMEOUT=15
   LOOKUP_COOLDOWN_SECONDS=15
   LOG_LEVEL=INFO
   ```

3. Attach one **persistent Volume** at `/data`; use a single polling worker replica.
4. Set the start command to `python main.py` (`Procfile` included). A website, public port, or dashboard is not necessary.
5. If you possess an **authorized read-only player data provider**, configure `PLAYER_LOOKUP_URL` as an HTTPS URL with exactly one `{player_id}` placeholder. Optional `PLAYER_LOOKUP_TOKEN` is sent in a `Bearer` header. Its JSON result must document the meaning of region fields. No provider is supplied by default.
6. Launch with `/start` in Telegram. Payments, if eligible, take place **on the official Midasbuy site**, not in Telegram.

## Local run and tests

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py
```

Windows PowerShell: `$env:BOT_TOKEN='your token'`; `$env:DB_PATH='.\\kro_geoip.db'` before `python main.py`.

The unit suite uses synthetic data. For the isolated HTTPS integration suite, install dependencies into a Python 3.11+ `.venv`, then run `bash lab/run.sh` on Linux with `unshare`, `ip`, and OpenSSL. The runner enables only loopback in a new network namespace and starts the real Telegram SDK against a local Bot API fixture. The database and TLS certificate are temporary. See [the verification report](VERIFICATION_REPORT_AR.md) for the executed checks and evidence.

## SQLite and backwards compatibility

Tables `lookups` and `operations` are stored on the volume. Existing `operations` tables are migrated automatically by adding a nullable `lookup_id` column; existing history is not deleted. A completed lookup does not mean a store region was changed, so the default audit entry is `NOT_PERFORMED` / `UNVERIFIED`.

## Security and scope

- No Midasbuy/PUBG login, browser cookies, payment details or third-party passwords are collected.
- No simulated `UAE → Turkey / Done` status; opening a public page is not state mutation.
- No region-IP spoofing or circumvention of restricted purchase channels.
- The project does not support payment processing from Telegram.

See `TURKEY_SHOP_INVESTIGATION_AR.md` and `TECHNICAL_RESEARCH_AR.md` for the investigation and technical limits. Official sources: Midasbuy glossary `https://developer.midasbuy.com/guide/integration/glossary` and restrictions `https://developer.midasbuy.com/guide/extensions`.

## Original offline region research lab

For a read-only diagnostic test of browser-session versus cross-session price observations:

```bash
python region_lab.py samples/session_specific.json
python region_lab.py samples/cross_session_display.json
python -m unittest discover -s tests -v
```

All examples are artificial. This does not send a Midasbuy API request, change a region, prove payment eligibility, or automate purchases. See `REGION_LAB_REPORT_AR.md`.

### تقييم أدلة حساب فعلي

شغّل `python region_lab.py evidence.json` على ملف يخص حساباً واحداً وباقة واحدة. يمكنك استخدام `account_evidence` وحده أو إرفاقه بملاحظات واجهة المتجر القديمة. المثال التالي اصطناعي. استخدم أسماء مرجعية محلية للقطات أو الإيصالات بعد إخفاء بيانات الهوية والدفع.

```json
{
  "sku": "DEMO_UC60",
  "account_evidence": {
    "before": {
      "region": {"user_region": "AE", "reference": "account_before"}
    },
    "after": {
      "region": {"user_region": "TR", "reference": "account_after"},
      "checkout": {
        "shop_country": "TR",
        "outcome": "quoted",
        "payment_method": "card",
        "reference": "checkout_after",
        "currency": "TRY",
        "total": "49.90"
      }
    }
  }
}
```

- **منطقة الحساب** تُدوَّن من قراءة صريحة لمنطقة اللاعب لدى جهة اللعبة قبل الإجراء وبعده.
- **الإجمالي** `quoted` يُدوَّن من صفحة الدفع قبل التأكيد شاملاً الرسوم المعروضة. استخدم `paid` مع المبلغ الفعلي من إيصال مكتمل. المقارنة تتطلب مرحلتين متطابقتين في حالة الدفع والعملة وطريقة الدفع، مع ثبات الباقة والكمية والعرض الترويجي.
- **نتيجة الدفع** استخدم `rejected` عند ظهور رفض صريح في صفحة الدفع واحذف `total` و`currency`. استخدم `paid` فقط عند وجود إيصال مكتمل يخص متجر تركيا والحساب والباقة. عرض الإجمالي يثبت الوصول إلى عرض السعر في الجلسة المسجّلة حسب الدليل المقدّم.

تظهر النتائج ضمن `account_evidence` بصفتها بيانات مدخلة يدوياً تحتاج إلى مراجعة مصادرها. القيمة `null` تعني أن المقارنة أو نتيجة متجر تركيا غير متاحة من المدخلات. للتحقق من منطقة اللاعب، استخدم قناة الدعم الرسمية للعبة. [دعم PUBG MOBILE](https://support.pubgmobile.com/?lang=en) · [تعريفات Midasbuy](https://developer.midasbuy.com/guide/integration/glossary)

## Prior test verification (20 Nov 2026)

See `TEST_RESULTS_AR.md`. The isolated test run passed **22 tests** including ten additional validation/regression tests. The region lab was patched so a single independent observation cannot be labeled as repeated across sessions. The bot does **not** contain a region-eligibility mutation provider. Live Telegram, Railway, Midasbuy, or payment actions were not tested.
