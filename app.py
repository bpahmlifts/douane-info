import io
import re
import time
from typing import Optional

import pandas as pd
import streamlit as st
from google import genai
from pydantic import BaseModel, Field

st.set_page_config(
    page_title="Product Verrijkingstool (Gemini)",
    page_icon="📦",
    layout="centered",
)

st.title("📦 Douane & Product Data Verrijker")
st.write(
    "Haal automatisch de **HS-code**, het **land van herkomst** en het **gewicht (kg)** op met behulp van de gratis Gemini API."
)
st.warning(
    "Alle resultaten zijn een **AI-voorstel**, geen officiële classificatie. "
    "Controleer HS-codes altijd in de EU TARIC-database of bij de douane. "
    "Het land van herkomst is een **indicatie** op basis van merk en producttype; "
    "het land van de leverancier is niet hetzelfde als het land van oorsprong. "
    "Vul bij twijfel de herkomst in uit de leveranciersfactuur of het oorsprongscertificaat."
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


# Pydantic schema met 8-cijferige GN/HS-code specificatie
class ProductEnrichment(BaseModel):
    hs_code: str = Field(
        description="De meest waarschijnlijke 8-cijferige EU Gecombineerde Nomenclatuur (GN / Combined Nomenclature) code, uitsluitend cijfers (bijv. 84313100 of 85171300)"
    )
    country_of_origin: str = Field(
        description="2-letterige ISO landcode (bijv. CN, DE, NL, IT) of 'UNKNOWN'"
    )
    weight_kg: Optional[float] = Field(
        default=None,
        description="Nettogewicht in kilogram (kg)"
    )
    confidence_notes: str = Field(
        description="Korte motivering en toelichting op de 8-cijferige GN/HS-indeling"
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


def clean(value) -> str:
    """Maak van een celwaarde een nette string; lege cellen en NaN worden ''."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def final_origin(ai_origin: str, own_origin: str):
    """Een eigen opgave (bijv. van de leveranciersfactuur) wint van de AI-indicatie."""
    if own_origin:
        return own_origin, "Eigen opgave"
    return ai_origin, "AI-indicatie"


def enrich_item(client: genai.Client, text: str) -> dict:
    prompt = f"""
    Je bent een douane- en logistiek data-expert. Analyseer het onderstaande product:
    \"\"\"{text}\"\"\"

    Bepaal de volgende gegevens:
    1. HS/GN-code: De exacte 8-cijferige Europese goederencode (bijv. 85171300 voor smartphones).
    2. Land van herkomst (Country of Origin):
       - Als het land in de tekst staat, neem dat over.
       - Staat het er NIET in, gebruik dan je algemene kennis over dit specifieke product of merk om het meest gangbare productieland te bepalen (bijvoorbeeld 'CN' voor iPhones die voornamelijk in China/India worden geassembleerd). Gebruik alleen 'UNKNOWN' als het volstrekt onbekend is.
       - Dit is een indicatie: het gaat om het land waar het product is gemaakt, niet om het land van een leverancier of doorverkoper.
    3. Gewicht (kg):
       - Als het gewicht in de tekst staat, reken dit om naar kg.
       - Staat het er NIET in, schat of benader dan het officiële nettogewicht van dit specifieke product op basis van je productkennis (bijv. ca. 0.200 kg voor een moderne Pro smartphone).
    4. Toelichting: Geef een korte toelichting op de keuzes en vermeld of het gewicht/land gebaseerd is op de tekst of op algemene specificaties.
    """

    # Snellere en minder belaste modellen eerst
    models_to_try = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]

    last_error = ""

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
            last_error = f"{model_name}: {str(e)[:100]}"
            # Direct door naar het volgende model zonder lange pauzes
            time.sleep(0.5)
            continue

    return {
        "hs_code": "ERROR",
        "country_of_origin": "ERROR",
        "weight_kg": None,
        "confidence_notes": f"Fout bij verwerken: {last_error}"
    }


tab1, tab2 = st.tabs(["Single Product Check", "📁 Bulk Upload (Excel/CSV)"])

with tab1:
    single_input = st.text_area(
        "Productomschrijving:",
        placeholder="Bijv: Optische sensor 24V DC, Made in Germany, gewicht 140g",
    )
    col_a, col_b = st.columns(2)
    single_supplier = col_a.text_input("Leverancier (optioneel):")
    single_own_origin = col_b.text_input(
        "Eigen herkomst (optioneel):",
        placeholder="ISO-code, bijv. DE",
        help="Staat deze ingevuld (bijv. van de leveranciersfactuur), dan wint die van de AI-indicatie.",
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

            origin, origin_source = final_origin(
                result["country_of_origin"], single_own_origin.strip()
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("HS Code", result["hs_code"])
            c2.metric("Herkomstland", origin, help=f"Bron: {origin_source}")
            c3.metric(
                "Gewicht",
                f"{result['weight_kg']} kg"
                if result["weight_kg"]
                else "Onbekend",
            )
            if single_supplier.strip():
                st.caption(f"Leverancier: {single_supplier.strip()}")
            st.caption(f"Herkomst: {origin_source}")
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

        NONE_OPTION = "(geen)"
        optional_columns = [NONE_OPTION] + list(df.columns)

        column_to_read = st.selectbox(
            "Kolom met omschrijvingen:", df.columns
        )
        supplier_column = st.selectbox(
            "Kolom met leverancier (optioneel):", optional_columns
        )
        own_origin_column = st.selectbox(
            "Kolom met eigen herkomst, bijv. uit leveranciersfactuur (optioneel):",
            optional_columns,
            help="Waar deze kolom is ingevuld, wint die van de AI-indicatie.",
        )

        if st.button("Start Bulk Verwerking", type="primary"):
            if not api_key:
                st.error("Geen API Key ingesteld.")
            else:
                client = genai.Client(api_key=api_key)
                progress_bar = st.progress(0)
                status_text = st.empty()

                hs_codes, ai_origins, final_origins, origin_sources = [], [], [], []
                weights, notes = [], []
                total = len(df)

                for n, (_, row) in enumerate(df.iterrows(), start=1):
                    val = str(row[column_to_read])
                    status_text.text(f"Regel {n} van {total}...")
                    res = enrich_item(client, val)

                    own_origin = (
                        clean(row[own_origin_column])
                        if own_origin_column != NONE_OPTION
                        else ""
                    )
                    origin, source = final_origin(res["country_of_origin"], own_origin)

                    hs_codes.append(res["hs_code"])
                    ai_origins.append(res["country_of_origin"])
                    final_origins.append(origin)
                    origin_sources.append(source)
                    weights.append(res["weight_kg"])
                    notes.append(res["confidence_notes"])

                    progress_bar.progress(n / total)

                df["HS_Code"] = hs_codes
                df["Country_of_Origin_AI"] = ai_origins
                df["Country_of_Origin"] = final_origins
                df["Origin_Source"] = origin_sources
                df["Weight_KG"] = weights
                df["Notes"] = notes
                if supplier_column != NONE_OPTION:
                    df["Supplier"] = [clean(v) for v in df[supplier_column]]

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
