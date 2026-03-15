import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import plotly.express as px
import google.generativeai as genai
import os

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA Y ESTILO
# ==========================================
st.set_page_config(page_title="BioSTEAM Interactive Lab", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_status_with_出席=True)

# ==========================================
# 2. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# ==========================================
def run_simulation(f_water, f_ethanol, t_feed, p_flash):
    # Limpiar flujos previos para evitar duplicidad de IDs
    bst.main_flowsheet.clear()
    
    # Configuración Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes
    mosto = bst.Stream("1_MOSTO", Water=f_water, Ethanol=f_ethanol, 
                       units="kg/hr", T=t_feed + 273.15, P=101325)
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, Ethanol=0, 
                                 units="kg/hr", T=95 + 273.15, P=300000)

    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                         outs=("3_Mosto_Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=92+273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=p_flash)
    
    # Tanque Flash (Manejo de Q=0 y P variable)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_caliente", "Vinazas"), P=p_flash, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25 + 273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Sistema
    eth_sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    eth_sys.simulate()
    
    return eth_sys

def generar_reporte(sistema):
    # Balance de Materia
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0.001:
            datos_mat.append({
                "ID": s.ID,
                "Temp (°C)": round(s.T - 273.15, 2),
                "Flujo (kg/h)": round(s.F_mass, 2),
                "% Etanol": round((s.imass["Ethanol"]/s.F_mass)*100, 2) if s.F_mass > 0 else 0
            })
    df_mat = pd.DataFrame(datos_mat)

    # Balance de Energía (Corrección de error .duty)
    datos_en = []
    for u in sistema.units:
        calor_kw = 0.0
        if isinstance(u, bst.HXprocess):
            calor_kw = (u.outs[0].H - u.ins[0].H) / 3600
        elif hasattr(u, "heat_utilities") and u.heat_utilities:
            calor_kw = sum([hu.duty for hu in u.heat_utilities]) / 3600
        
        if abs(calor_kw) > 0.001:
            datos_en.append({"Equipo": u.ID, "Energía (kW)": round(calor_kw, 2)})
            
    return df_mat, pd.DataFrame(datos_en)

# ==========================================
# 3. INTERFAZ DE USUARIO (SIDEBAR)
# ==========================================
st.sidebar.title("⚙️ Parámetros de Proceso")
f_w = st.sidebar.slider("Flujo Agua (kg/h)", 500, 2000, 900)
f_e = st.sidebar.slider("Flujo Etanol (kg/h)", 50, 500, 100)
t_in = st.sidebar.number_input("Temp. Entrada (°C)", value=25)
p_fl = st.sidebar.number_input("Presión Flash (Pa)", value=101325)

# ==========================================
# 4. EJECUCIÓN Y VISUALIZACIÓN
# ==========================================
st.title("🧪 Simulación BioSTEAM: Destilación Flash")
st.info("Ajusta los parámetros en la izquierda y observa el balance en tiempo real.")

try:
    sys = run_simulation(f_w, f_e, t_in, p_fl)
    df_m, df_e = generar_reporte(sys)

    # Layout de columnas
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📊 Balance de Materia")
        st.dataframe(df_m, use_container_width=True)
        
        # Gráfico de barras de pureza
        fig_pureza = px.bar(df_m, x="ID", y="% Etanol", title="Pureza de Etanol por Corriente", color="% Etanol")
        st.plotly_chart(fig_pureza, use_container_width=True)

    with col2:
        st.subheader("⚡ Consumo Energético")
        if not df_e.empty:
            fig_en = px.pie(df_e, values=df_e["Energía (kW)"].abs(), names="Equipo", title="Distribución de Calor")
            st.plotly_chart(fig_en, use_container_width=True)
        else:
            st.write("Sin datos de energía significativos.")

    # --- DIAGRAMA PFD ---
    st.subheader("🖼️ Diagrama de Flujo (PFD)")
    sys.diagram(file="pfd", format="png")
    st.image("pfd.png")

    # --- INTEGRACIÓN GEMINI ---
    st.divider()
    st.subheader("🤖 Consultoría IA (Gemini)")
    
    if st.button("Analizar resultados con IA"):
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-2.5-pro')
            
            prompt = f"""
            Como experto en Ingeniería Química, analiza estos datos de BioSTEAM:
            {df_m.to_string()}
            
            1. ¿Es eficiente la separación en el equipo V1?
            2. ¿Cómo afecta el flujo de entrada a la recuperación de etanol?
            3. Da una recomendación técnica breve.
            """
            with st.spinner("Gemini está analizando el proceso..."):
                response = model.generate_content(prompt)
                st.write(response.text)
        else:
            st.error("Error: No se encontró la GEMINI_API_KEY en los Secrets de Streamlit.")

except Exception as e:
    st.error(f"Error en la simulación: {e}")
