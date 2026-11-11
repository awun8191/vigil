# Data Parsing / Extraction Quality Testing

> General methodology for validating CSV/Excel data parsing quality — applicable whenever user uploads or provides structured data files. Use during ultrawork verification phases or standalone.

## Two-Phase Testing Approach

### Phase 1: Schema Inference Tests
*Verify header-to-field mapping works across real-world column name variations:*

1. Identify the schema fields your system needs (e.g., `title, price, currency, category, description, tags, stock_quantity`)
2. Collect common header name patterns from real user data:
   - Nigerian vendor exports: `Product Name, Price (₦), CUR, About`
   - Instagram-style: `Caption, Price, Location`
   - Wholesale: `PRODUCT NAME, PRICE, CATEGORY` (all-caps)
   - French/West African: `Product, Prix, Catégorie`
3. Test with:
   - **Missing columns**: What if there's no `currency` or `description` column?
   - **Extra columns**: What if there are `Size, Color, Material, SKU, Weight` columns?
   - **Reversed order**: What if price is the first column and title is last?
4. For each, check: did the system map the right header to the right field?

### Phase 2: Data Type Integrity Tests
*Verify field-level values are correctly parsed despite format variations:*

| Field | Variations to Test | Common Nigerian Vendor Patterns |
|-------|-------------------|-------------------------------|
| **title** | Empty, whitespace-only, all-caps, HTML-tagged, 500+ chars, emoji-only, numbers-only | `🔥 2-in-1 Turbo Fan 🔥`, `MEN'S FORMAL SHOES BLACK`, `<b>Product</b>` |
| **price** | Comma-formatted, symbol-prefixed (₦/N/$/€), ranges, "Call for price", "Free", decimals, absurd values | `₦72,986`, `N20,000`, `$25.99`, `₦15,000 - ₦20,000`, `Call for price` |
| **currency** | Codes (NGN/USD/GHS), symbols (₦/$/€), local names (Naira/Cedis), missing | `NGN`, `Naira`, `₦`, empty → default NGN |
| **category** | Misspellings, variants, all-caps, multiple values, numbers, empty | `Fasion`→Fashion, `Electornics`→Electronics, `Clothing`→Fashion |
| **description** | HTML tags, URLs, phone numbers, mixed languages, emojis | `<b>Premium</b>`, `Yoruba: Eyi ni ọja... English: Good product` |
| **stock_quantity** | Text indicators, ranges, fractions, qualitative, empty | `In stock`→1, `Out of stock`→0, `10-20`→10, `2.5 dozen`→2, `Plenty`→positive |
| **tags** | Comma-separated, semicolon-separated, space-separated | `premium, leather, brown` → [`premium`, `leather`, `brown`] |

## Test File Generation Pattern

Define test cases as data structures, not CSV files:

```python
def add_test(test_id, description, headers, rows, expected_checks):
    TESTS.append({
        "id": test_id,
        "description": description,
        "headers": headers,
        "rows": rows,
        "checks": expected_checks,  # List of {row, field, expected} dicts
    })
```

This keeps tests maintainable and self-documenting.

## Common Pitfalls

- **Price ranges without separator detection**: Raw regex extraction on `"₦15,000 - ₦20,000"` strips `-` too, merging digits → `1500020000`. Fix: split on range separators first, extract from first part only.
- **HTML stripping loses spacing**: `<h1>Title</h1><p>Sub</p>` → `TitleSub` if you just remove tags. Fix: replace closing tags with a space before stripping.
- **Absurd prices from multi-pack errors**: Jumia data sometimes has prices like `₦1,720,425,000` (multi-pack multiplication errors). Add a configurable sanity cap (e.g., ₦500M).
- **Textual stock indicators not handled**: "In stock", "Yes", "Available" are not numbers but indicate positive stock. Map them to minimum positive value (1).
- **Case-sensitive category matching**: "Fashion" and "fashion" should resolve to the same canonical value. Normalize to lowercase before matching.

## Verification

After fixing bugs found by these tests:
1. Run the project's existing test suite (should still pass)
2. Run the data type integrity tests (should show 100%)
3. Run schema inference tests (should show 100%)
4. Verify with a real sample file from the user's data
