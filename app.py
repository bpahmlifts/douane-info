import io
import re
from typing import Optional
from google import genai
import pandas as pd
from pydantic import BaseModel, Field
import streamlit as st

st.set_page_config(
    page_title="Product Verrijkingstool (Gemini)",
    page_icon="📦",
    layout="centered",
)

st.title("📦 Douane & Product Data Verrijker")
st.write(
    "Haal automatisch de **HS-code**, het **land van herkomst** en het **gewicht (kg)** op met behulp van de gratis Gemini API."
)

# Sidebar voor instellingen
with st.sidebar:
    st.header("⚙️ Instellingen")
    # Zoekt eerst in Streamlit Secrets, anders via invoerveld
    default_key = st.secrets.get("GEMINI_API_KEY", "")
    api_key = (
        st.text_input(
            "Gemini API Key:", value=default_key, type="password"
        )
        if not default_key
        else default_key
    )
    if default_key:
        st.success("API Key geladen via Secrets!")
    st.caption("Gratis API-key aanmaken via aistudio.google.com")


# Pydantic schema
class ProductEnrichment(BaseModel):
    hs_code: str = Field(
        description="Meest waarschijnlijke 6-cijferige Harmonized System (HS) code"
    )
    country_of_origin: str = Field(
        description="2-letterige ISO landcode (bijv. CN, DE, NL) of 'UNKNOWN'"
    )
    weight_kg: Optional[float] = Field(
        default=None, description="Gewicht in kilogram (kg)"
    )
    confidence_notes: str = Field(
        description="Korte motivering van de classificatie"
    )


def extract_weight_regex(text: str) -> Optional[float]:
    pattern = r"(\d+(?:[.,]\d+)?)\s*(kg|g|gram|kilogram|lbs?|oz)\b"
    match = re.search(pattern, str(text), re.IGNORECASE)
    if not match:
        return None
    val_str, unit = match.groups()
    val = float(val_str.replace(",", "."))
    unit = unit.lower()
    if unit in ("kg", "kilogram"):
        return round(val, 3)
    if unit in ("g", "gram"):
        return round(val / 1000, 3)
    if unit in ("lb", "lbs"):
        return round(val * 0.45359237, 3)
    if unit == "oz":
        return round(val * 0.0283495, 3)
    return None


import time


import time

import time

def enrich_item(client: genai.Client, text: str) -> dict:
    prompt = f"""
    Analyseer de onderstaande productomschrijving en bepaal:
    1. De 6-cijferige HS code (Harmonized System).
    2. Land van oorsprong (ISO 3166-1 alpha-2, bijv. CN, NL, DE). Geef 'UNKNOWN' indien niet te herleiden.
    3. Nettogewicht in kilogram (kg).
    
    Product:
    \"\"\"{text}\"\"\"
    """
    
    # Modellen die we één voor één proberen
    models_to_try = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]
    
    errors_logged = []

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": ProductEnrichment,
                },
            )
            res = ProductEnrichment.model_validate_json(response.text).model_dump()
            if res["weight_kg"] is None:
                res["weight_kg"] = extract_weight_regex(text)
            return res
        except Exception as e:
            errors_logged.append(f"{model_name}: {str(e)[:80]}")
            time.sleep(1)
            continue
            
    return {
        "hs_code": "ERROR",
        "country_of_origin": "ERROR",
        "weight_kg": None,
        "confidence_notes": " | ".join(errors_logged)
    }
tab1, tab2 = st.tabs(["Single Product Check", "📁 Bulk Upload (Excel/CSV)"])

with tab1:
    single_input = st.text_area(
        "Productomschrijving:",
        placeholder="Bijv: Optische sensor 24V DC, Made in Germany, gewicht 140g",
    )
    if st.button("Verrijk product", type="primary"):
        if not api_key:
            st.error("Vul eerst een Gemini API Key in.")
        elif not single_input.strip():
            st.warning("Vul een productomschrijving in.")
        else:
            client = genai.Client(api_key=api_key)
            with st.spinner("Classificeren via Gemini..."):
                result = enrich_item(client, single_input)

            c1, c2, c3 = st.columns(3)
            c1.metric("HS Code", result["hs_code"])
            c2.metric("Herkomstland", result["country_of_origin"])
            c3.metric(
                "Gewicht",
                f"{result['weight_kg']} kg"
                if result["weight_kg"]
                else "Onbekend",
            )
            st.info(f"**Toelichting:** {result['confidence_notes']}")

with tab2:
    uploaded_file = st.file_uploader(
        "Upload bestand", type=["xlsx", "csv"]
    )
    if uploaded_file:
        df = (
            pd.read_csv(uploaded_file)
            if uploaded_file.name.endswith(".csv")
            else pd.read_excel(uploaded_file)
        )
        st.write("Voorvertoning:")
        st.dataframe(df.head(3))

        column_to_read = st.selectbox(
            "Kolom met omschrijvingen:", df.columns
        )

        if st.button("Start Bulk Verwerking", type="primary"):
            if not api_key:
                st.error("Geen API Key ingesteld.")
            else:
                client = genai.Client(api_key=api_key)
                progress_bar = st.progress(0)
                status_text = st.empty()

                hs_codes, origins, weights, notes = [], [], [], []
                total = len(df)

                for idx, row in df.iterrows():
                    val = str(row[column_to_read])
                    status_text.text(f"Regel {idx + 1} van {total}...")
                    res = enrich_item(client, val)

                    hs_codes.append(res["hs_code"])
                    origins.append(res["country_of_origin"])
                    weights.append(res["weight_kg"])
                    notes.append(res["confidence_notes"])

                    progress_bar.progress((idx + 1) / total)

                df["HS_Code"] = hs_codes
                df["Country_of_Origin"] = origins
                df["Weight_KG"] = weights
                df["Notes"] = notes

                status_text.success("Verwerking voltooid!")
                st.dataframe(df.head())

                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False)
                buffer.seek(0)

                st.download_button(
                    label="📥 Download verrijkt Excel-bestand",
                    data=buffer,
                    file_name="producten_verrijkt.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                