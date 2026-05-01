Group Members:
Kiki Quinn: kvquinn
Victoria Luzniak: valuznia
Hailey Franks: hefranks
Yinqiao Wang: ywang256
Maxime Thai: mthai


# Recipe Finder Demo App
A Streamlit-based recipe finder app for a group course project. This app helps users enter ingredients they already have, select food restrictions, and receive recipe suggestions from a curated local recipe dataset.

## Project Overview
This project was designed as a stable demo application for a course project (E298). Instead of relying on a live third-party recipe API, the app uses a local JSON dataset. This avoids issues with API ownership, billing, usage limits, and internet-related failures during presentations.

The app allows users to:
- enter available ingredients
- select dietary restrictions
- filter by meal type
- search recipe titles
- view matching recipes ranked by ingredient overlap
- see which ingredients they already have and which ones are still missing
- scale recipe proportions to match desired number of servings

## Features

- Ingredient-based recipe search
- Dietary restriction filtering
- Meal type selection
- Recipe title keyword search
- Match score ranking
- Missing ingredient display
- Expandable recipe instructions
- Local dataset for stable demos
- AI-generated nutritional summaries 

## Tech Stack

- Python
- Streamlit
- JSON dataset storage
- Gemini 2.5 Flash

## Project Structure

```text
recipe-finder-group-project/
├── TestStreamlit.py
├── recipes.json
├── requirements.txt
└── README.md
