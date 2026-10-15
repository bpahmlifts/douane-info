
import io
import re
from typing import Optional
import pandas as pd
from pydantic import BaseModel, Field
import streamlit as st
from openai import OpenAI

# Paginainstellingen
st.set_page_config(
    page_title="Product Verrijkingstool", page_icon="📦", layout="centered"
)

st.title("📦 Douane & Product Data Verrijker")
st.write(
    "Haal automatisch de **HS-code**, het **land van herkomst** en het **gewicht (kg)** op uit productomschrijvingen."
)


# Sidebar voor instellingen
with st.sidebar:
    st.header("⚙️ Instellingen")
    api_key = st.text_input("OpenAI API Key:", type="password")
    model_choice = st.selectbox("Model:", ["gpt-4o-mini", "gpt-4o"], index=0)
    st.caption("Gebruik gpt-4o-mini voor snelle en voordelige verwerking.")


# Data structuur via Pydantic
class ProductEnrichment(BaseModel):
    hs_code: str = Field(
        description="Meest waarschijnlijke 6-cijferige HS code"
    )
    country_of_origin: str = Field(
        description="2-letterige ISO-code van het land van herkomst, of 'UNKNOWN'"
    )
    weight_kg: Optional[float] = Field(
        default=None, description="Gewicht in kilogram (omgerekend)"
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


def enrich_item(client: OpenAI, text: str, model: str) -> dict:
    prompt = f"""
    Analyseer de onderstaande productomschrijving en bepaal:
    1. De 6-cijferige HS code (Harmonized System).
    2. Land van oorsprong (ISO 3166-1 alpha-2, bijv. CN, NL, DE). Geef 'UNKNOWN' indien niet te herleiden.
    3. Nettogewicht in kilogram (kg).
    
    Product:
    \"\"\"{text}\"\"\"
    """
    try:
        response = client.beta.chat.completions.parse(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Je bent een douane- en logistiek data-expert.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format=ProductEnrichment,
        )
        res = response.choices[0].message.parsed.model_dump()
        if res["weight_kg"] is None:
            res["weight_kg"] = extract_weight_regex(text)
        return res
    except Exception as e:
        return {
            "hs_code": "ERROR",
            "country_of_origin": "ERROR",
            "weight_kg": None,
            "confidence_notes": str(e),
        }


# Tabs: Enkelvoudige invoer vs Bulk Excel
tab1, tab2 = st.tabs(
    ["Single Product Check", "📁 Bulk Upload (Excel of CSV)"]
)

# --- TAB 1: Enkel product ---
with tab1:
    single_input = st.text_area(
        "Productomschrijving of specificaties:",
        placeholder="Bijv: Apple iPhone 15 Pro Max 256GB Titanium - Made in China - Gewicht: 221 g",
    )
    if st.button("Verrijk enkel product", type="primary"):
        if not api_key:
            st.error("Vul eerst je OpenAI API Key in het menu links in.")
        elif not single_input.strip():
            st.warning("Vul een productomschrijving in.")
        else:
            client = OpenAI(api_key=api_key)
            with st.spinner("Gegevens opzoeken..."):
                result = enrich_item(client, single_input, model_choice)

            col1, col2, col3 = st.columns(3)
            col1.metric("HS Code", result["hs_code"])
            col2.metric("Land van herkomst", result["country_of_origin"])
            col3.metric(
                "Gewicht",
                f"{result['weight_kg']} kg"
                if result["weight_kg"]
                else "Onbekend",
            )
            st.info(f"**Toelichting:** {result['confidence_notes']}")

# --- TAB 2: Bulk upload ---
with tab2:
    uploaded_file = st.file_uploader(
        "Upload Excel- of CSV-bestand", type=["xlsx", "csv"]
    )
    if uploaded_file:
        df = (
            pd.read_csv(uploaded_file)
            if uploaded_file.name.endswith(".csv")
            else pd.read_excel(uploaded_file)
        )
        st.write("Voorvertoning van je bestand:")
        st.dataframe(df.head(3))

        column_to_read = st.selectbox(
            "Selecteer de kolom met productomschrijvingen:", df.columns
        )

        if st.button("Start Bulk Verwerking", type="primary"):
            if not api_key:
                st.error("Vul eerst je OpenAI API Key in het menu links in.")
            else:
                client = OpenAI(api_key=api_key)
                progress_bar = st.progress(0)
                status_text = st.empty()

                hs_codes, origins, weights, notes = [], [], [], []
                total = len(df)

                for idx, row in df.iterrows():
                    val = str(row[column_to_read])
                    status_text.text(f"Bezig met regel {idx + 1} van {total}...")
                    res = enrich_item(client, val, model_choice)

                    hs_codes.append(res["hs_code"])
                    origins.append(res["country_of_origin"])
                    weights.append(res["weight_kg"])
                    notes.append(res["confidence_notes"])

                    progress_bar.progress((idx + 1) / total)

                df["HS_Code"] = hs_codes
                df["Country_of_Origin"] = origins
                df["Weight_KG"] = weights
                df["Notes"] = notes

                status_text.success("Klaar!")
                st.dataframe(df.head())

                # Download knop
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
