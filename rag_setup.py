"""
rag_setup.py — Teleone.in Store Indexer
========================================
Run ONCE: python rag_setup.py
Then run: python server.py

This scrapes all Teleone product pages, adds Hinglish product
knowledge manually, embeds everything, and saves to ./store_index
"""

import os, re, time
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import chromadb

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ══════════════════════════════════════════════════════
# ALL TELEONE PAGES TO SCRAPE
# ══════════════════════════════════════════════════════
YOUR_PAGES = [
    "https://www.teleone.in/collections/shop-by-categories",
    "https://www.teleone.in/collections/best-selling-products",
    "https://www.teleone.in/collections/combos",
    "https://www.teleone.in/collections/new-arrivals",
    # --- Product pages ---
    "https://www.teleone.in/products/vedacharya-karela-jamun-powder-controls-diabetic-problem",
    "https://www.teleone.in/products/ever-slim-capsules-for-quick-weight-loss",
    "https://www.teleone.in/products/vedacharya-kumkumadi-facial-oil-for-naturally-glowing-skin",
    "https://www.teleone.in/products/vedacharya-ashwagandha-powder",
    "https://www.teleone.in/products/vedacharya-pahadi-oil",
    "https://www.teleone.in/products/taakatvati-ayurvedic-tablets-for-weight-gain",
    "https://www.teleone.in/products/vedacharya-triphala-powder",
    "https://www.teleone.in/products/vedacharya-adivasi-herbal-hair-oil-for-hair-growth",
    "https://www.teleone.in/products/deemark-daily-multivitamin-softgels-capsules",
    "https://www.teleone.in/products/bnalcodrops-to-stopaddiction",
    # --- Category pages ---
    "https://www.teleone.in/collections/diabetes",
    "https://www.teleone.in/collections/weight-management",
    "https://www.teleone.in/collections/hair-care",
    "https://www.teleone.in/collections/skin-care",
    "https://www.teleone.in/collections/superfood",
    "https://www.teleone.in/collections/pain-relief",
    "https://www.teleone.in/collections/digestive-health",
    "https://www.teleone.in/collections/muscle-gain-fitness",
    "https://www.teleone.in/collections/addiction",
]

# ══════════════════════════════════════════════════════
# MANUAL HINGLISH PRODUCT KNOWLEDGE
# (This is the most important part — Priya speaks from this)
# Add product name in Hindi too so customers find it easily
# ══════════════════════════════════════════════════════
HINGLISH_KNOWLEDGE = """
=== TELEONE.IN STORE INFORMATION ===

Store: Teleone — India ka Number 1 Teleshopping Website
Website: www.teleone.in
Payment: UPI, Credit Card, Debit Card, Net Banking, COD (Cash on Delivery) available hai
Delivery: All India delivery hoti hai. Free shipping bhi milti hai certain orders pe.
Contact: Teleone ki website pe contact page available hai.
Return Policy: 7 din ka return policy hai genuine products ke liye.
All products ayurvedic aur natural ingredients se bane hain.

=== PRODUCTS — DIABETES ===

Product: Vedacharya Karela Jamun Powder
Hindi mein: करेला जामुन पाउडर
Kaam kya karta hai: Diabetes control karta hai, blood sugar normal rakhta hai, blood purifier hai.
Ingredients: Karela (bitter gourd) aur Jamun (black plum) — dono diabetes ke liye best natural ingredients hain.
Kaun le sakta hai: Jo log diabetes se pareshan hain, blood sugar high rehta hai.
Kaise lein: Subah khali pet paani ke saath lein.
Price: Teleone website pe check karein — discount available hai.
Link: https://www.teleone.in/products/vedacharya-karela-jamun-powder-controls-diabetic-problem

=== PRODUCTS — WEIGHT LOSS ===

Product: Ever Slim Capsules
Hindi mein: वजन घटाने की कैप्सूल
Kaam kya karta hai: Jaldi weight loss karta hai, fat burn karta hai, metabolism boost karta hai.
Kaun le sakta hai: Jo log motapa kam karna chahte hain, pet ki charbi hatana chahte hain.
Natural formula hai, side effects nahi hote.
Link: https://www.teleone.in/products/ever-slim-capsules-for-quick-weight-loss

=== PRODUCTS — WEIGHT GAIN ===

Product: Taakatvati Ayurvedic Tablets
Hindi mein: ताकतवटी — वजन बढ़ाने की आयुर्वेदिक टेबलेट
Kaam kya karta hai: Wajan badhata hai, body banaata hai, kamzori door karta hai, bhook badhata hai.
Kaun le sakta hai: Jo log bahut patle hain, wajan badhana chahte hain, gym karte hain.
Ayurvedic hai, safe hai.
Link: https://www.teleone.in/products/taakatvati-ayurvedic-tablets-for-weight-gain

=== PRODUCTS — SKIN CARE ===

Product: Vedacharya Kumkumadi Facial Oil
Hindi mein: कुमकुमादि फेशियल ऑयल — चमकती त्वचा के लिए
Kaam kya karta hai: Skin glowing banata hai, dark spots hatata hai, natural glow aata hai face pe.
Kaun le sakta hai: Jo log dull skin se pareshan hain, dark spots, tan, ya skin glow chahte hain.
Saffron aur ayurvedic herbs se bana hai.
Link: https://www.teleone.in/products/vedacharya-kumkumadi-facial-oil-for-naturally-glowing-skin

=== PRODUCTS — HAIR CARE ===

Product: Vedacharya Adivasi Herbal Hair Oil
Hindi mein: आदिवासी हर्बल हेयर ऑयल — बाल उगाने वाला तेल
Kaam kya karta hai: Baal ugata hai, baal jhadna band karta hai, baalon ko lamba aur ghana karta hai.
Kaun le sakta hai: Jo log hair fall se pareshan hain, baal patale hain ya takla ho rahe hain.
Adivasi herbal formula — natural ingredients se bana hai.
Link: https://www.teleone.in/products/vedacharya-adivasi-herbal-hair-oil-for-hair-growth

Product: Vedacharya Pahadi Oil
Hindi mein: पहाड़ी तेल
Kaam kya karta hai: Pahadi jadibutiyon se bana special oil — baalon aur health dono ke liye useful.
Link: https://www.teleone.in/products/vedacharya-pahadi-oil

=== PRODUCTS — STRENGTH & ENERGY ===

Product: Vedacharya Ashwagandha Powder
Hindi mein: अश्वगंधा पाउडर — ताकत aur energy ke liye
Kaam kya karta hai: Kamzori door karta hai, stress kam karta hai, immunity badhata hai, energy deta hai.
Kaun le sakta hai: Jo log thake rehte hain, stress mein hain, ya body strength chahte hain.
Pure Himalayan Ashwagandha hai.
Link: https://www.teleone.in/products/vedacharya-ashwagandha-powder

Product: Vedacharya Triphala Powder
Hindi mein: त्रिफला पाउडर — पेट साफ़ रखने के लिए
Kaam kya karta hai: Pet saaf karta hai, constipation door karta hai, digestion theek karta hai, immunity badhata hai.
Teen fruits ka mix: Amla, Baheda, Harad — sab ayurvedic.
Link: https://www.teleone.in/products/vedacharya-triphala-powder

=== PRODUCTS — VITAMINS ===

Product: Deemark Daily Multivitamin Softgels
Hindi mein: रोज़ की विटामिन कैप्सूल
Kaam kya karta hai: Roz ki vitamins aur minerals ki kami poori karta hai, energy deta hai, immunity strong karta hai.
Kaun le sakta hai: Sabke liye — jo log vitamins ki kami feel karte hain.
Link: https://www.teleone.in/products/deemark-daily-multivitamin-softgels-capsules

=== PRODUCTS — ADDICTION (NASHA MUKTI) ===

Product: BN-ALCO Drops (नशा मुक्ति दवा)
Hindi mein: बीएन-एल्को ड्रॉप्स — शराब, सिगरेट, तंबाकू छुड़ाने की दवा
Kaam kya karta hai: Sharaab peena band karata hai, cigarette chhudaata hai, tobacco ki aadat chhudaata hai.
Homeopathic medicine hai — safe hai, no side effects.
Reviews: 1224 se zyada positive reviews hain.
Price: Rs 2,499 (originally Rs 5,940 — 57% OFF)
Link: https://www.teleone.in/products/bnalcodrops-to-stopaddiction

=== COMMON CUSTOMER QUESTIONS ===

Q: Kya ye products genuine hain?
A: Haan, Teleone India ka No.1 teleshopping website hai. Saare products ayurvedic aur certified hain.

Q: Delivery kitne din mein aati hai?
A: Generally 5-7 working days mein delivery ho jaati hai. Location ke hisaab se vary kar sakta hai.

Q: COD available hai?
A: Haan, Cash on Delivery available hai.

Q: Koi side effects hain?
A: Saare products natural ayurvedic ingredients se bane hain. Side effects nahi hote. Phir bhi doctor se consult kar sakte hain.

Q: Return kaise karein?
A: 7 din ka return policy hai. Teleone website pe contact karein ya customer care se baat karein.

Q: Discount ya offer hai?
A: Haan, website pe 40-60% tak ke offers available rehte hain. BN-ALCO pe abhi 57% OFF chal raha hai.
"""

CHUNK_SIZE    = 200
CHUNK_OVERLAP = 40


def scrape_page(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TeleoneBot/1.0)"}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["nav","footer","script","style","noscript","header","aside","iframe","button"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        # Keep only meaningful content (skip if mostly nav garbage)
        if len(text) < 100:
            return ""
        print(f"  ✅ {url.split('/products/')[-1].split('/collections/')[-1][:40]} → {len(text)} chars")
        return text
    except Exception as e:
        print(f"  ❌ Failed {url.split('/')[-1]}: {e}")
        return ""


def chunk_text(text: str) -> list:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i : i + CHUNK_SIZE])
        if len(chunk.strip()) > 40:
            chunks.append(chunk)
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def main():
    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY missing in .env — add it first!")
        return

    print("\n" + "═"*52)
    print("  🏪 Teleone RAG Setup — Building Priya's Knowledge")
    print("═"*52)

    # 1. Add Hinglish manual knowledge first
    print("\n📝 Adding Hinglish product knowledge...")
    all_text = HINGLISH_KNOWLEDGE + "\n\n"

    # 2. Scrape live pages
    print("\n📥 Scraping Teleone pages...")
    for url in YOUR_PAGES:
        text = scrape_page(url)
        if text:
            all_text += text + "\n\n"
        time.sleep(0.6)

    print(f"\n  Total knowledge: {len(all_text)} characters")

    # 3. Chunk
    print("\n✂️  Splitting into chunks...")
    chunks = chunk_text(all_text)
    print(f"  Created {len(chunks)} chunks")

    # 4. Embed + store
    print("\n🧠 Creating embeddings (takes ~1 min)...")
    client = OpenAI(api_key=OPENAI_API_KEY)
    db = chromadb.PersistentClient(path="./store_index")

    try:
        db.delete_collection("mystore")
        print("  Cleared old index")
    except:
        pass

    collection = db.create_collection("mystore")

    batch_size = 50
    total = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        try:
            resp = client.embeddings.create(input=batch, model="text-embedding-3-small")
            embeddings = [e.embedding for e in resp.data]
            collection.add(
                documents=batch,
                embeddings=embeddings,
                ids=[f"c_{i+j}" for j in range(len(batch))]
            )
            total += len(batch)
            print(f"  Indexed {total}/{len(chunks)}...")
        except Exception as e:
            print(f"  ❌ Batch error: {e}")

    print(f"\n✅ Done! {total} chunks saved to ./store_index")
    print("\n  Now run your bot:")
    print("  python server.py\n")


if __name__ == "__main__":
    main()