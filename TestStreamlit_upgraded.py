import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set

import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="Recipe Finder", page_icon="🍳", layout="wide")

# Securely fetch the key from secrets
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    st.error("API Key not found. Please set 'GEMINI_API_KEY' in Streamlit Secrets.")

APP_DIR = Path(__file__).resolve().parent
DB_FILE = APP_DIR / "recipe_app.db"
DATA_FILE_CANDIDATES = [
    APP_DIR / "recipes_for_app.json",
    APP_DIR / "recipes.json",
    APP_DIR / "recipes_for_app_sample_20.json",
]

RESTRICTION_RULES = {
    "Vegetarian": {"exclude": ["chicken", "turkey", "beef", "pork", "fish", "shrimp", "salmon", "tuna", "bacon"]},
    "Vegan": {"exclude": ["chicken", "turkey", "beef", "pork", "fish", "shrimp", "salmon", "tuna", "egg", "milk", "cheese", "butter", "yogurt", "honey"]},
    "Gluten-Free": {"exclude": ["pasta", "tortilla", "bread", "flour", "soy sauce", "gluten", "breadcrumbs", "ramen", "bagel"]},
    "Dairy-Free": {"exclude": ["milk", "cheese", "butter", "cream", "yogurt", "dairy", "mozzarella", "parmesan", "feta"]},
    "Egg-Free": {"exclude": ["egg", "mayonnaise"]},
    "Nut-Free": {"exclude": ["peanut", "almond", "walnut", "cashew", "pecan", "nut", "pesto"]},
}

DEFAULT_IMAGE = "https://images.unsplash.com/photo-1490645935967-10de6ba17061?auto=format&fit=crop&w=1200&q=80"

UNITS_AND_NOISE = {
    "cup", "cups", "tablespoon", "tablespoons", "tbsp", "teaspoon", "teaspoons", "tsp",
    "ounce", "ounces", "oz", "pound", "pounds", "lb", "lbs", "gram", "grams", "g",
    "kilogram", "kilograms", "kg", "ml", "l", "liter", "liters", "pinch", "dash",
    "small", "medium", "large", "extra", "fresh", "dried", "optional", "divided",
    "taste", "finely", "roughly", "thinly", "thick", "thickly", "peeled", "quartered",
    "cored", "sliced", "chopped", "minced", "diced", "shredded", "grated", "beaten",
    "thawed", "cold", "warm", "hot", "room", "temperature", "plus", "more", "for",
    "serving", "garnish", "package", "packages", "can", "cans", "jar", "jars", "stick",
    "sticks", "piece", "pieces", "about", "into", "inch", "inches", "halved", "whole",
    "boneless", "skinless", "trimmed", "lean", "seeded", "to", "the", "and", "or",
    "with", "of", "a", "an"
}

MEAL_TYPE_RULES = {
    "Breakfast": ["breakfast", "pancake", "waffle", "oatmeal", "egg", "omelet", "muffin", "granola", "toast"],
    "Lunch": ["salad", "sandwich", "wrap", "soup", "bowl"],
    "Dinner": ["dinner", "pasta", "curry", "stew", "roast", "skillet", "casserole", "burger"],
    "Snack": ["snack", "dip", "cookie", "bar", "smoothie", "dessert"],
}

def get_data_file() -> Path:
    for path in DATA_FILE_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No recipe dataset found.")

def init_db() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS favorites (user_id INTEGER NOT NULL, recipe_id TEXT NOT NULL, PRIMARY KEY (user_id, recipe_id), FOREIGN KEY (user_id) REFERENCES users(id))")
        conn.commit()

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or hashlib.sha256(str(Path.cwd()).encode("utf-8") + password.encode("utf-8")).hexdigest()[:32]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return digest.hex(), salt

def verify_user(username: str, password: str) -> Optional[int]:
    username = username.strip().lower()
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute("SELECT id, password_hash, salt FROM users WHERE username = ?", (username,)).fetchone()
    if not row: return None
    user_id, stored_hash, salt = row
    password_hash, _ = hash_password(password, salt)
    return int(user_id) if hmac.compare_digest(password_hash, stored_hash) else None

def get_favorite_ids(user_id: int) -> Set[str]:
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT recipe_id FROM favorites WHERE user_id = ?", (user_id,)).fetchall()
    return {str(row[0]) for row in rows}

def add_favorite(user_id: int, recipe_id: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO favorites (user_id, recipe_id) VALUES (?, ?)", (user_id, str(recipe_id)))
        conn.commit()

def remove_favorite(user_id: int, recipe_id: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?", (user_id, str(recipe_id)))
        conn.commit()

def parse_minutes(value) -> Optional[int]:
    if value is None: return None
    if isinstance(value, (int, float)): return int(value)
    text = str(value).strip().lower()
    total = 0
    hour_match = re.search(r"(\d+)\s*h", text)
    minute_match = re.search(r"(\d+)\s*m", text)
    if hour_match: total += int(hour_match.group(1)) * 60
    if minute_match: total += int(minute_match.group(1))
    return total if total > 0 else (int(text) if text.isdigit() else None)

def parse_manual_ingredients(text: str) -> List[str]:
    parts = re.split(r"[,\n]", text or "")
    return sorted(list(set(p.strip().lower() for p in parts if p.strip())))

def infer_difficulty(total_minutes: Optional[int], ingredient_count: int, instruction_count: int) -> str:
    score = 0
    if total_minutes and total_minutes > 30: score += 1
    if total_minutes and total_minutes > 60: score += 1
    if ingredient_count > 12: score += 1
    if instruction_count > 5: score += 1
    return "Easy" if score <= 1 else ("Medium" if score == 2 else "Hard")

def normalize_recipe(recipe: Dict) -> Dict:
    ingredients = recipe.get("ingredients", []) or []
    if isinstance(ingredients, str): ingredients = [i.strip() for i in ingredients.split(",") if i.strip()]
    instructions = recipe.get("instructions", []) or []
    if isinstance(instructions, str): instructions = [i.strip() for i in instructions.split(".") if i.strip()]
    
    total_minutes = parse_minutes(recipe.get("cook_time"))
    if not total_minutes:
        times = recipe.get("times", {}) or {}
        total_minutes = parse_minutes(times.get("total")) or parse_minutes(times.get("cook")) or 30

    return {
        "id": str(recipe.get("id", recipe.get("title", "recipe"))),
        "title": recipe.get("title", "Untitled Recipe"),
        "ingredients": ingredients,
        "instructions": instructions,
        "meal_type": recipe.get("meal_type", "Dinner"),
        "cook_time": total_minutes,
        "difficulty": recipe.get("difficulty") or infer_difficulty(total_minutes, len(ingredients), len(instructions)),
        "image": recipe.get("image") or DEFAULT_IMAGE,
        "rating": recipe.get("rating"),
        "source_url": recipe.get("source_url") or recipe.get("url") or "",
        "allergen_tags": recipe.get("allergen_tags", []),
        "diet_tags": recipe.get("diet_tags", [])
    }

@st.cache_data
def load_recipes() -> List[Dict]:
    with get_data_file().open("r", encoding="utf-8") as f:
        return [normalize_recipe(r) for r in json.load(f)]

def violates_restrictions(recipe: Dict, restrictions: List[str]) -> bool:
    _text = " ".join(item.lower() for item in recipe["ingredients"])
    recipe_allergens = {item.lower() for item in recipe.get("allergen_tags", [])}
    recipe_diet_tags = {item.lower() for item in recipe.get("diet_tags", [])}

    for restriction in restrictions:
        excluded = set(RESTRICTION_RULES[restriction]["exclude"])
        if any(word in _text for word in excluded): return True
        if restriction == "Vegetarian" and "vegetarian" not in recipe_diet_tags and any(word in _text for word in excluded): return True
    return False

def score_recipe(recipe: Dict, available: List[str]) -> Dict:
    _text = " ".join(item.lower() for item in recipe["ingredients"])
    matched = [item for item in available if item.lower() in _text]
    
    result = dict(recipe)
    result["matched"] = sorted(set(matched))
    result["score"] = len(result["matched"])
    result["coverage"] = result["score"] / max(len(recipe["ingredients"]), 1)
    return result

def find_recipes(recipes: List[Dict], available: List[str], restrictions: List[str], meal_type: str, keyword: str, max_time: int, difficulties: List[str], sort_by: str, minimum_matches: int) -> List[Dict]:
    filtered = []
    for recipe in recipes:
        if meal_type != "All" and recipe["meal_type"] != meal_type: continue
        if keyword.lower() not in recipe["title"].lower(): continue
        if recipe["cook_time"] > max_time: continue
        if difficulties and recipe["difficulty"] not in difficulties: continue
        if violates_restrictions(recipe, restrictions): continue

        scored = score_recipe(recipe, available)
        if scored["score"] >= minimum_matches: filtered.append(scored)

    reverse = sort_by != "Shortest cooking time"
    key_func = (lambda x: x["cook_time"]) if not reverse else (lambda x: (x["score"], x["coverage"]))
    filtered.sort(key=key_func, reverse=reverse)
    return filtered

def get_ai_recipe_enhancements(recipe, target_servings):
    """Uses Gemini to scale ingredients and generate a nutritional summary."""
    prompt = f"""
    Scale ingredients for {target_servings} people and provide a 1-sentence nutrition summary.
    Recipe: {recipe['title']}
    Ingredients: {recipe['ingredients']}
    
    Return the response in this EXACT JSON format:
    {{
        "scaled_ingredients": ["list of strings"],
        "ai_summary": "string"
    }}
    """
    try:
        response = model.generate_content(prompt)
        # More robust JSON cleaning to prevent parsing errors
        clean_text = re.search(r'\{.*\}', response.text, re.DOTALL)
        if clean_text:
            return json.loads(clean_text.group())
        return None
    except Exception as e:
        return None

def render_recipe_card(recipe: Dict, favorite_ids: Set[str], logged_in: bool, user_id: Optional[int], key_prefix: str, serving_size: int) -> None:
    with st.container(border=True):
        left, right = st.columns([1, 2])
        left.image(recipe["image"], use_container_width=True)
        with right:
            st.subheader(recipe["title"])
            st.write(f"**Time:** {recipe['cook_time']} min | **Difficulty:** {recipe['difficulty']}")
            
            st.markdown("---")
            if st.button(f"✨ AI Insights for {serving_size} servings", key=f"ai_{key_prefix}_{recipe['id']}"):
                with st.spinner("Analyzing..."):
                    ai_data = get_ai_recipe_enhancements(recipe, serving_size)
                    if ai_data:
                        st.info(f"**AI Health Note:** {ai_data['ai_summary']}")
                        # FIXED: Changed 'Scaled s' to 'Scaled ingredients' to match prompt
                        with st.expander(f"📍 Scaled ingredients for {serving_size}"):
                            for ing in ai_data.get("scaled_ingredients", []): st.write(f"• {ing}")
                    else:
                        st.error("AI could not be reached. Check your API key or connection.")
            st.markdown("---")

            if recipe["matched"]: st.success("Matched: " + ", ".join(recipe["matched"]))
            
            if logged_in:
                if recipe["id"] in favorite_ids:
                    if st.button("Unsave", key=f"unsave_{key_prefix}_{recipe['id']}"):
                        remove_favorite(user_id, recipe["id"])
                        st.rerun()
                elif st.button("Save", key=f"save_{key_prefix}_{recipe['id']}"):
                    add_favorite(user_id, recipe["id"])
                    st.rerun()

            with st.expander("Ingredients & Steps"):
                for ing in recipe["ingredients"]: st.write(f"- {ing}")
                for idx, step in enumerate(recipe["instructions"], 1): st.write(f"{idx}. {step}")

# Init
init_db()
RECIPES = load_recipes()
RECIPE_LOOKUP = {r["id"]: r for r in RECIPES}

with st.sidebar:
    st.header("Search")
    selected_ingredients = st.multiselect("Ingredients", options=sorted(list(set([i for r in RECIPES for i in r["ingredients"][:3]])))[:500])
    manual_text = st.text_area("Manual additions")
    keyword = st.text_input("Search titles")
    restrictions = st.multiselect("Restrictions", options=list(RESTRICTION_RULES.keys()))
    meal_type = st.selectbox("Meal", ["All", "Breakfast", "Lunch", "Dinner"])
    max_time = st.slider("Max Time", 10, 180, 60)
    sort_by = st.selectbox("Sort", ["Best match", "Shortest cooking time"])
    serving_size = st.number_input("Servings", 1, 20, 4)

available = sorted(list(set(selected_ingredients + parse_manual_ingredients(manual_text))))
results = find_recipes(RECIPES, available, restrictions, meal_type, keyword, max_time, [], sort_by, 0)

st.title("Recipe Finder")
for r in results[:10]:
    render_recipe_card(r, set(), False, None, "results", serving_size)
