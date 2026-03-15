import sys

# ==========================================
# 0. PARCHE DE COMPATIBILIDAD (ALTAIR V4/V5)
# ==========================================
try:
    import altair.vegalite.v4 as hv
except ImportError:
    try:
        import altair.vegalite.v5 as hv
        sys.modules['altair.vegalite.v4'] = hv
    except ImportError:
        pass

import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import plotly.express as px
import google.generativeai as genai

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="BioSTEAM Lab - Destilación", layout="wide")

st.markdown("""
    <style>
    .stMetric { background-color: #ffffff; padding: 10px; border-radius: 10px; border: 1px solid #e0e0e0; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. MOTOR DE SIMULACIÓN
# ==========================================
def run_simulation(f_water, f_ethanol, t_feed, p_flash):
    # Reset para evitar duplicidad de IDs en cada ejecución
    bst.main_flowsheet.clear()
    
    # Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes de entrada
    mosto = bst.Stream("1_MOSTO", Water=f_water, Ethanol=f_ethanol, 
                       units="kg/hr", T=t_feed + 273.15, P=101325)
    
    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=200, Ethanol=0, 
                                 units="kg/hr", T=95 + 273.15, P=300000)

    # Diseño de la planta
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                         outs=("Mosto_Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15 # Especificación de diseño
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla_Caliente", T=92+273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bifasica", P=p_flash)
    
    # Separación Flash (Se maneja el error de duty asegurando que Q=0)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor_rico_EtOH", "Vinazas"), P=p_flash, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto_Final", T=25 + 273.15)
    
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Resolución del Sistema
    eth_sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    eth_sys.simulate()
    
    return eth_sys

def obtener_reportes(sistema):
    # Datos de corrientes
    materia = []
    for s in sistema.streams:
        if s.F_mass > 0:
            materia.append({
                "Corriente": s.ID,
                "Temp (°C)": round(s.T - 273.15, 1),
                "Flujo (kg/h)": round(s.F_mass, 1),
                "EtOH %": round((s.imass["Ethanol"]/s.F_mass)*100, 2) if s.F_mass > 0 else 0
            })
    
    # Datos de energía (Corrección específica para evitar errores de atributo)
    energia = []
    for u in sistema.units:
        # HXProcess usa balance de entalpía
        if isinstance(u, bst.HXprocess):
            q = (u.outs[0].H - u.ins[0].H) / 3600
            energia.append({"Equipo": u.ID, "Carga (kW)": round(q, 2), "Tipo": "Recuperación"})
        # Equipos con servicios auxiliares
        elif hasattr(u, 'heat_utilities') and u.heat_utilities:
            q = sum([hu.duty for hu in u.heat_utilities]) / 3600
            if abs(q) > 0.01:
                tipo = "Calentamiento" if q > 0 else "Enfriamiento"
                energia.append({"Equipo": u.ID, "Carga (kW)": round(q, 2), "Tipo": tipo})
    
    return pd.DataFrame(materia), pd.DataFrame(energia)

# ==========================================
# 3. INTERFAZ STREAMLIT
# ==========================================
st.title("🚀 BioSTEAM Web: Simulador de Bioetanol")
st.markdown("---")

# Sidebar
st.sidebar.header("🔧 Parámetros de Simulación")
f_h2o = st.sidebar.slider("Flujo Agua Alimento (kg/h)", 500, 1500, 900)
f_etoh = st.sidebar.slider("Flujo Etanol Alimento (kg/h)", 50, 200, 100)
p_sep = st.sidebar.number_input("Presión Separador Flash (Pa)", value=101325, step=5000)

if st.sidebar.button("▶️ Ejecutar Simulación", type="primary"):
    try:
        with st.spinner("Calculando balances..."):
            sys = run_simulation(f_h2o, f_etoh, 25, p_sep)
            df_m, df_e = obtener_reportes(sys)

        # KPIs superiores
        prod_final = df_m[df_m["Corriente"] == "Producto_Final"]["Flujo (kg/h)"].values[0]
        pureza_final = df_m[df_m["Corriente"] == "Producto_Final"]["EtOH %"].values[0]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Producción Final", f"{prod_final} kg/h")
        c2.metric("Pureza Etanol", f"{pureza_final} %")
        c3.metric("Energía Total (Calor)", f"{df_e[df_e['Carga (kW)'] > 0]['Carga (kW)'].sum():.2f} kW")

        # Visualizaciones
        tab1, tab2, tab3 = st.tabs(["📊 Balances", "⚡ Energía", "📐 Diagrama"])

        with tab1:
            st.subheader("Balance de Materia")
            st.dataframe(df_m, use_container_width=True)
            fig_m = px.bar(df_m, x="Corriente", y="EtOH %", color="Temp (°C)", title="Concentración de Etanol por Etapa")
            st.plotly_chart(fig_m, use_container_width=True)

        with tab2:
            st.subheader("Análisis Térmico")
            col_a, col_b = st.columns(2)
            with col_a:
                st.table(df_e)
            with col_b:
                fig_e = px.pie(df_e[df_e['Carga (kW)'].abs() > 0], values=df_e['Carga (kW)'].abs(), names='Equipo', title="Distribución de Cargas Térmicas")
                st.plotly_chart(fig_e)

        with tab3:
            st.subheader("Diagrama de Proceso (PFD)")
            sys.diagram(file="pfd_web", format="png")
            st.image("pfd_web.png")

        # --- SECCIÓN IA ---
        st.markdown("---")
        st.subheader("🤖 Consultoría Técnica Gemini")
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-2.5-pro')
            
            prompt = f"Actúa como un Ingeniero Senior. Analiza este balance de materia: {df_m.to_string()}. ¿Qué opinas de la pureza alcanzada en 'Producto_Final' considerando una presión de {p_sep} Pa? Dame 2 consejos técnicos."
            
            with st.spinner("Consultando con la IA..."):
                res = model.generate_content(prompt)
                st.info(res.text)
        else:
            st.warning("Configura tu GEMINI_API_KEY en los Secrets de Streamlit para habilitar la consultoría IA.")

    except Exception as e:
        st.error(f"Se produjo un error técnico: {e}")
else:
    st.write("Presiona el botón en la barra lateral para iniciar la simulación.")
