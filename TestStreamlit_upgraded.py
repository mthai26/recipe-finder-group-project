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
    raise FileNotFoundError("No recipe dataset found. Put recipes_for_app.json or recipes.json next to this script.")

def init_db() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                recipe_id TEXT NOT NULL,
                PRIMARY KEY (user_id, recipe_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.commit()

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or hashlib.sha256(str(Path.cwd()).encode("utf-8") + password.encode("utf-8")).hexdigest()[:32]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return digest.hex(), salt

def create_user(username: str, password: str) -> tuple[bool, str]:
    username = username.strip().lower()
    if len(username) < 3:
        return False, "Username must be at least 3 characters long."
    if len(password) < 6:
        return False, "Password must be at least 6 characters long."

    password_hash, salt = hash_password(password)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
                (username, password_hash, salt),
            )
            conn.commit()
        return True, "Account created successfully. Please log in."
    except sqlite3.IntegrityError:
        return False, "That username already exists."

def verify_user(username: str, password: str) -> Optional[int]:
    username = username.strip().lower()
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            "SELECT id, password_hash, salt FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row:
        return None

    user_id, stored_hash, salt = row
    password_hash, _ = hash_password(password, salt)
    if hmac.compare_digest(password_hash, stored_hash):
        return int(user_id)
    return None

def get_favorite_ids(user_id: int) -> Set[str]:
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT recipe_id FROM favorites WHERE user_id = ?", (user_id,)
        ).fetchall()
    return {str(row[0]) for row in rows}

def add_favorite(user_id: int, recipe_id: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, recipe_id) VALUES (?, ?)",
            (user_id, str(recipe_id)),
        )
        conn.commit()

def remove_favorite(user_id: int, recipe_id: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?",
            (user_id, str(recipe_id)),
        )
        conn.commit()

def parse_minutes(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().lower()
    if not text:
        return None

    total = 0
    hour_match = re.search(r"(\d+)\s*h", text)
    minute_match = re.search(r"(\d+)\s*m", text)
    plain_minute_match = re.search(r"(\d+)\s*min", text)

    if hour_match:
        total += int(hour_match.group(1)) * 60
    if minute_match:
        total += int(minute_match.group(1))
    elif plain_minute_match:
        total += int(plain_minute_match.group(1))
    elif text.isdigit():
        total += int(text)

    return total or None

def parse_manual_ingredients(text: str) -> List[str]:
    """Cleans the manual text input into a list of individual ingredients."""
    parts = re.split(r"[,\n]", text or "")
    cleaned = []
    for part in parts:
        item = part.strip().lower()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned

def infer_difficulty(total_minutes: Optional[int], ingredient_count: int, instruction_count: int) -> str:
    score = 0
    if total_minutes:
        if total_minutes > 60:
            score += 2
        elif total_minutes > 30:
            score += 1
    if ingredient_count > 12:
        score += 1
    if instruction_count > 5:
        score += 1

    if score <= 1:
        return "Easy"
    if score == 2:
        return "Medium"
    return "Hard"

def infer_meal_type(recipe: Dict) -> str:
    values = " ".join(
        str(v) for v in [recipe.get("title", ""), recipe.get("category", ""), " ".join(recipe.get("tags", []))]
    ).lower()
    for meal_type, words in MEAL_TYPE_RULES.items():
        if any(word in values for word in words):
            return meal_type
    return "Dinner"

def normalize_recipe(recipe: Dict) -> Dict:
    ingredients = recipe.get("ingredients", []) or []
    if isinstance(ingredients, str):
        ingredients = [item.strip() for item in ingredients.split(",") if item.strip()]

    instructions = recipe.get("instructions", []) or []
    if isinstance(instructions, str):
        instructions = [item.strip() for item in instructions.split(".") if item.strip()]

    total_minutes = parse_minutes(recipe.get("cook_time"))
    if total_minutes is None:
        times = recipe.get("times", {}) or {}
        total_minutes = parse_minutes(times.get("total")) or parse_minutes(times.get("cook")) or parse_minutes(times.get("prep"))

    difficulty = recipe.get("difficulty")
    if not difficulty:
        difficulty = infer_difficulty(total_minutes, len(ingredients), len(instructions))

    meal_type = recipe.get("meal_type") or infer_meal_type(recipe)

    normalized = {
        "id": str(recipe.get("id", recipe.get("slug", recipe.get("title", "recipe")))),
        "title": recipe.get("title", "Untitled Recipe"),
        "ingredients": ingredients,
        "instructions": instructions,
        "diet_tags": recipe.get("diet_tags", []),
        "allergen_tags": recipe.get("allergen_tags", []),
        "meal_type": meal_type,
        "cook_time": total_minutes if total_minutes is not None else 30,
        "difficulty": difficulty,
        "image": recipe.get("image") or DEFAULT_IMAGE,
        "rating": recipe.get("rating"),
        "source_url": recipe.get("source_url") or recipe.get("url") or "",
        "category": recipe.get("category", ""),
        "tags": recipe.get("tags", []),
    }
    return normalized

@st.cache_data
def load_recipes() -> List[Dict]:
    data_file = get_data_file()
    with data_file.open("r", encoding="utf-8") as f:
        raw_data = json.load(f)

    recipes = [normalize_recipe(recipe) for recipe in raw_data]
    return recipes

def _candidates_from_line(line: str) -> Set[str]:
    text = line.lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\d+[\/\d\.-]*", " ", text)
    text = re.sub(r"[^a-z\s-]", " ", text)
    tokens = [token for token in re.split(r"\s+", text) if token and token not in UNITS_AND_NOISE and len(token) > 1]
    if not tokens:
        return set()

    candidates = set()
    for token in tokens:
        if len(token) > 2:
            candidates.add(token)

    if len(tokens) >= 2:
        for i in range(len(tokens) - 1):
            pair = f"{tokens[i]} {tokens[i + 1]}".strip()
            if all(word not in UNITS_AND_NOISE for word in pair.split()):
                candidates.add(pair)

    if len(tokens) >= 2:
        candidates.add(" ".join(tokens[-2:]))
    candidates.add(tokens[-1])
    return {c.strip() for c in candidates if c.strip()}

@st.cache_data
def build_ingredient_options(recipes: List[Dict]) -> List[str]:
    counts: Dict[str, int] = {}
    for recipe in recipes:
        for line in recipe["ingredients"]:
            for candidate in _candidates_from_line(line):
                counts[candidate] = counts.get(candidate, 0) + 1

    keep = [name for name, count in counts.items() if count >= 2 and len(name) <= 24]
    keep.sort(key=lambda item: (-counts[item], item))
    return keep[:900]

def violates_restrictions(recipe: Dict, restrictions: List[str]) -> bool:
    # Changed 's' to 'ingredients'
    ingredient_text = " ".join(item.lower() for item in recipe["ingredients"])
    recipe_allergens = {item.lower() for item in recipe.get("allergen_tags", [])}
    recipe_diet_tags = {item.lower() for item in recipe.get("diet_tags", [])}

    for restriction in restrictions:
        excluded = set(RESTRICTION_RULES[restriction]["exclude"])
        if any(word in ingredient_text for word in excluded):
            return True
        if restriction == "Vegetarian" and "vegetarian" not in recipe_diet_tags and any(word in ingredient_text for word in excluded):
            return True
        if restriction == "Vegan" and "vegan" not in recipe_diet_tags and any(word in ingredient_text for word in excluded):
            return True
        if restriction == "Gluten-Free" and "gluten" in recipe_allergens:
            return True
        if restriction == "Dairy-Free" and "dairy" in recipe_allergens:
            return True
        if restriction == "Egg-Free" and "egg" in recipe_allergens:
            return True
        if restriction == "Nut-Free" and "nuts" in recipe_allergens:
            return True
    return False

def score_recipe(recipe: Dict, available: List[str]) -> Dict:
    # Changed 's' to 'ingredients'
    ingredient_text = " ".join(item.lower() for item in recipe["ingredients"])
    matched = []
    for item in available:
        item = item.lower().strip()
        if item and item in ingredient_text:
            matched.append(item)

    matched = sorted(set(matched))
    missing = []
    score = len(matched)
    coverage = score / max(len(recipe["ingredients"]), 1)

    result = dict(recipe)
    result["matched"] = matched
    result["missing"] = missing
    result["score"] = score
    result["coverage"] = coverage
    return result

def find_recipes(
    recipes: List[Dict],
    available: List[str],
    restrictions: List[str],
    meal_type: str,
    keyword: str,
    max_time: int,
    difficulties: List[str],
    sort_by: str,
    minimum_matches: int,
) -> List[Dict]:
    filtered = []
    keyword = keyword.strip().lower()

    for recipe in recipes:
        if meal_type != "All" and recipe["meal_type"] != meal_type:
            continue
        if keyword and keyword not in recipe["title"].lower():
            continue
        if recipe["cook_time"] > max_time:
            continue
        if difficulties and recipe["difficulty"] not in difficulties:
            continue
        if violates_restrictions(recipe, restrictions):
            continue

        scored = score_recipe(recipe, available)
        if scored["score"] < minimum_matches:
            continue
        filtered.append(scored)

    if sort_by == "Best ingredient match":
        filtered.sort(key=lambda x: (x["score"], x["coverage"], x.get("rating") or 0, -x["cook_time"]), reverse=True)
    elif sort_by == "Shortest cooking time":
        filtered.sort(key=lambda x: (x["cook_time"], -(x.get("rating") or 0)))
    else:
        filtered.sort(key=lambda x: ((x.get("rating") or 0), x["score"], x["coverage"]), reverse=True)
    return filtered

def get_ai_recipe_enhancements(recipe, target_servings):
    """Uses Gemini to scale ingredients and generate a nutritional summary."""
    prompt = f"""
    You are a professional chef and nutritionist. 
    Original Recipe: {recipe['title']}
    Original Ingredients: {recipe['ingredients']}
    
    Task 1: Scale these ingredients to serve exactly {target_servings} people.
    Task 2: Provide a 1-sentence nutritional summary (e.g., 'High protein, low calories' or 'Higher than average cholesterol').
    
    Return the response in this EXACT JSON format:
    {{
        "scaled_ingredients": ["list of strings"],
        "ai_summary": "string"
    }}
    """
    try:
        response = model.generate_content(prompt)
        json_str = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(json_str)
    except Exception as e:
        return None

def render_recipe_card(
    recipe: Dict,
    favorite_ids: Set[str],
    logged_in: bool,
    user_id: Optional[int],
    key_prefix: str,
    serving_size: int,
) -> None:
    with st.container(border=True):
        left, right = st.columns([1.0, 2.0])

        with left:
            st.image(recipe["image"], use_container_width=True)

        with right:
            st.subheader(recipe["title"])
            m1, m2, m3, m4 = st.columns(4)
            m1.write(f"**Meal type:** {recipe['meal_type']}")
            m2.write(f"**Time:** {recipe['cook_time']} min")
            m3.write(f"**Difficulty:** {recipe['difficulty']}")
            rating_text = f"{recipe['rating']:.1f} / 5" if isinstance(recipe.get("rating"), (int, float)) else "N/A"
            m4.write(f"**Rating:** {rating_text}")

            st.markdown("---")
            if st.button(f"✨ Generate AI Insights for {serving_size} servings", key=f"ai_btn_{key_prefix}_{recipe['id']}"):
                with st.spinner("Gemini is analyzing the kitchen..."):
                    ai_data = get_ai_recipe_enhancements(recipe, serving_size)
                    
                    if ai_data:
                        st.info(f"**AI Health Note:** {ai_data['ai_summary']}")
                        
                        # Corrected access key from 'scaled_s' to 'scaled_ingredients'
                        with st.expander(f"📍 Scaled ingredients for {serving_size}", expanded=True):
                            for ing in ai_data.get('scaled_ingredients', []):
                                st.write(f"• {ing}")
                    else:
                        st.error("AI could not be reached. Check your API key!")
            st.markdown("---")

            if recipe["matched"]:
                st.success("Ingredient matches: " + ", ".join(recipe["matched"]))
            else:
                st.info("No exact matches yet. You can still view the recipe details below.")

            if recipe.get("source_url"):
                st.markdown(f"[Open original recipe]({recipe['source_url']})")

            action_col1, action_col2 = st.columns([1, 4])
            with action_col1:
                if logged_in and user_id is not None:
                    if recipe["id"] in favorite_ids:
                        if st.button("Unsave", key=f"{key_prefix}_unsave_{recipe['id']}"):
                            remove_favorite(user_id, recipe["id"])
                            st.rerun()
                    else:
                        if st.button("Save", key=f"{key_prefix}_save_{recipe['id']}"):
                            add_favorite(user_id, recipe["id"])
                            st.rerun()
                else:
                    st.caption("Log in to save favorites")

            with st.expander("See full ingredients and steps"):
                st.write("**Ingredients**")
                for ingredient in recipe["ingredients"]:
                    st.write(f"- {ingredient}")
                st.write("**Instructions**")
                for idx, step in enumerate(recipe["instructions"], start=1):
                    st.write(f"{idx}. {step}")

def ensure_session() -> None:
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user_id" not in st.session_state:
        st.session_state.user_id = None
    if "username" not in st.session_state:
        st.session_state.username = ""

init_db()
ensure_session()
RECIPES = load_recipes()
INGREDIENT_OPTIONS = build_ingredient_options(RECIPES)
RECIPE_LOOKUP = {recipe["id"]: recipe for recipe in RECIPES}

st.title("Recipe Finder Demo App")
st.caption(
    "Choose ingredients from smart suggestions, filter recipes by time and difficulty, open the original source, and save favorites with a simple account system."
)

with st.sidebar:
    st.header("Account")
    if st.session_state.logged_in:
        st.success(f"Logged in as {st.session_state.username}")
        if st.button("Log out"):
            st.session_state.logged_in = False
            st.session_state.user_id = None
            st.session_state.username = ""
            st.rerun()
    else:
        login_tab, signup_tab = st.tabs(["Log in", "Create account"])
        with login_tab:
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            if st.button("Log in now"):
                user_id = verify_user(st.session_state.login_username, st.session_state.login_password)
                if user_id is not None:
                    st.session_state.logged_in = True
                    st.session_state.user_id = user_id
                    st.session_state.username = st.session_state.login_username.strip().lower()
                    st.success("Logged in successfully.")
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")
        with signup_tab:
            st.text_input("New username", key="new_username")
            st.text_input("New password", type="password", key="new_password")
            if st.button("Create account"):
                ok, message = create_user(st.session_state.new_username, st.session_state.new_password)
                if ok:
                    st.success(message)
                else:
                    st.error(message)

    st.header("Search settings")
    selected_ingredients = st.multiselect(
        "Choose ingredients",
        options=INGREDIENT_OPTIONS,
        key="main_ingredient_selector",
        help="Start typing a letter such as e and Streamlit will suggest matches like egg, eggplant, or edamame when they exist in the dataset.",
    )
    manual_ingredient_text = st.text_area(
        "Add manual ingredients",
        placeholder="Example: garlic, rice, tomato",
        height=90,
        help="Use this if you want to add an ingredient that is not shown in the suggestions.",
    )
    keyword = st.text_input("Search recipe title", placeholder="Example: curry, pasta, tacos")
    selected_restrictions = st.multiselect("Food restrictions", options=list(RESTRICTION_RULES.keys()))
    selected_meal_type = st.selectbox("Meal type", options=["All", "Breakfast", "Lunch", "Dinner", "Snack"])
    max_time = st.slider("Maximum cooking time (minutes)", min_value=10, max_value=240, value=90, step=5)
    selected_difficulties = st.multiselect("Difficulty", options=["Easy", "Medium", "Hard"], default=["Easy", "Medium", "Hard"])
    minimum_matches = st.slider("Minimum ingredient matches", min_value=0, max_value=5, value=1, step=1)
    sort_by = st.selectbox("Sort results by", options=["Best ingredient match", "Shortest cooking time", "Highest rating"])

    serving_size = st.number_input("Number of people to serve", min_value=1, max_value=20, value=4)

available_ingredients = sorted(set(selected_ingredients + parse_manual_ingredients(manual_ingredient_text)))
results = find_recipes(
    RECIPES,
    available_ingredients,
    selected_restrictions,
    selected_meal_type,
    keyword,
    max_time,
    selected_difficulties,
    sort_by,
    minimum_matches,
)

favorite_ids = get_favorite_ids(st.session_state.user_id) if st.session_state.logged_in and st.session_state.user_id else set()
favorite_recipes = [RECIPE_LOOKUP[recipe_id] for recipe_id in favorite_ids if recipe_id in RECIPE_LOOKUP]
favorite_recipes.sort(key=lambda r: r["title"])

metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
metric_col1.metric("Recipes in dataset", len(RECIPES))
metric_col2.metric("Ingredients selected", len(available_ingredients))
metric_col3.metric("Matching recipes", len(results))
metric_col4.metric("Saved favorites", len(favorite_ids))

if available_ingredients:
    st.write("**Selected ingredients:**", ", ".join(available_ingredients))
else:
    st.info("Start typing in the ingredient selector in the sidebar to get smart ingredient suggestions.")

results_tab_labels = ["Recommended recipes"]
if st.session_state.logged_in:
    results_tab_labels.append("My favorites")

tabs = st.tabs(results_tab_labels)

with tabs[0]:
    if not results:
        st.warning("No matching recipes found. Try fewer restrictions, a longer maximum cooking time, or more ingredients.")
    else:
        for recipe in results:
            render_recipe_card(recipe,
                                favorite_ids,
                                st.session_state.logged_in,
                                st.session_state.user_id,
                                key_prefix="results",
                                serving_size=serving_size
                              )

if st.session_state.logged_in:
    with tabs[1]:
        if not favorite_recipes:
            st.info("You have not saved any favorites yet.")
        else:
            for recipe in favorite_recipes:
                favorite_scored = dict(recipe)
                favorite_scored["matched"] = []
                render_recipe_card(
                                    favorite_scored,
                                    favorite_ids,
                                    True,
                                    st.session_state.user_id,
                                    key_prefix="favorites",
                                    serving_size=serving_size
                                )

st.markdown("---")
st.markdown(
    "This upgraded demo keeps everything local for easier class sharing. The account system uses a simple SQLite database, which is fine for a demo or a small shared deployment. For a larger public app, you would usually move authentication and saved favorites to a cloud database and a proper auth service."
)
