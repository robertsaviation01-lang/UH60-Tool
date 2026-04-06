"""
Sidebar UI component for UH-60 Maintenance Modeling App
"""
import streamlit as st
import pandas as pd
import requests
import json
import re
from pathlib import Path


MRO_OVERHEAD_KEYS = [
    "mro_cost_insurance",
    "mro_cost_facility",
    "mro_cost_gse",
    "mro_cost_tooling",
    "mro_cost_engine_bay",
    "mro_cost_rotables_store",
    "mro_cost_parts_store",
    "mro_cost_utilities",
    "mro_cost_it_quality",
]


@st.cache_data(ttl=3600, show_spinner=False)
def get_live_fx_rate(from_currency, to_currency):
    if from_currency == to_currency:
        timestamp = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M:%S")
        return 1.0, timestamp, "Frankfurter API"

    url = f"https://api.frankfurter.app/latest?from={from_currency}&to={to_currency}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    payload = response.json()
    rate = float(payload["rates"][to_currency])
    rate_date = payload.get("date", pd.Timestamp.now().strftime("%Y-%m-%d"))
    timestamp = pd.to_datetime(rate_date).strftime("%d/%m/%Y")
    return rate, timestamp, "Frankfurter API"


def _scenario_folder():
    folder = Path("data") / "scenarios"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sanitize_scenario_name(name):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip())
    return cleaned.strip("_")


def _parse_date(value, default_date):
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return default_date


def _apply_loaded_scenario(data):
    today = pd.Timestamp.now().normalize().date()

    fleet_size = int(data.get("fleet_size", 1))
    fleet_size = min(max(fleet_size, 1), 100)

    st.session_state["currency_select"] = data.get("currency", "USD")
    st.session_state["use_live_fx_toggle"] = bool(data.get("use_live_fx", False))
    st.session_state["fleet_size_input"] = fleet_size
    st.session_state["annual_hours_input"] = int(data.get("annual_hours_per_ac", 600))
    loaded_mode = str(data.get("maintenance_mode", "Scheduled Event Library (detailed PMI)")).strip().lower()
    mode_map = {
        "manual mode": "Parts Supply Only",
        "part only": "Parts Supply Only",
        "parts only": "Parts Supply Only",
        "parts supply only": "Parts Supply Only",
        "scheduled & unscheduled maintenance": "Scheduled & Unscheduled Event Library (all events)",
        "scheduled and unscheduled maintenance": "Scheduled & Unscheduled Event Library (all events)",
        "scheduled and unscheduled event library": "Scheduled & Unscheduled Event Library (all events)",
        "scheduled event library (detailed pmi)": "Scheduled Event Library (detailed PMI)",
        "scheduled & unscheduled event library": "Scheduled & Unscheduled Event Library (all events)",
        "scheduled & unscheduled event library (all events)": "Scheduled & Unscheduled Event Library (all events)",
    }
    st.session_state["maintenance_mode_radio"] = mode_map.get(loaded_mode, "Scheduled Event Library (detailed PMI)")
    st.session_state["annual_escalation_input"] = float(data.get("annual_escalation", 5.0))
    st.session_state["planning_start_date_input"] = _parse_date(data.get("planning_start_date"), today)
    st.session_state["use_custom_ac_dates_checkbox"] = bool(data.get("use_custom_ac_dates", False))
    st.session_state["planning_horizon_years"] = int(data.get("years", 1))
    st.session_state["target_availability_input"] = float(data.get("target_availability", 75.0))
    st.session_state["labour_rate_input"] = float(data.get("labour_rate", 115.0))
    st.session_state["labour_cost_input"] = float(data.get("labour_cost", 45.0))
    st.session_state["mgmt_fee_usd"] = float(data.get("mgmt_fee_usd", 10000.0))
    st.session_state["geographic_contingency_pct_input"] = float(data.get("geographic_contingency_pct", 0.0))
    for overhead_key in MRO_OVERHEAD_KEYS:
        st.session_state[overhead_key] = float(data.get(overhead_key, 0.0))

    loaded_hours = data.get("hours_until_pmi", [])
    loaded_dates = data.get("custom_ac_dates", [])
    planning_start = st.session_state["planning_start_date_input"]
    for i in range(fleet_size):
        hours_val = loaded_hours[i] if i < len(loaded_hours) else 480
        st.session_state[f"ac_{i+1}_pmi"] = int(min(max(int(hours_val), 0), 480))

        date_val = loaded_dates[i] if i < len(loaded_dates) else planning_start
        st.session_state[f"ac_{i+1}_start_date"] = _parse_date(date_val, planning_start)

    st.session_state["scenario_last_loaded"] = str(data.get("name", "uploaded scenario"))
    st.session_state["_skip_pmi_default_init_once"] = True


def _collect_scenario_payload():
    payload = {
        "name": st.session_state.get("scenario_name_input", "scenario"),
        "saved_at": pd.Timestamp.now().isoformat(),
        "currency": st.session_state.get("currency_select", "USD"),
        "use_live_fx": bool(st.session_state.get("use_live_fx_toggle", False)),
        "fleet_size": int(st.session_state.get("fleet_size_input", 1)),
        "annual_hours_per_ac": int(st.session_state.get("annual_hours_input", 600)),
        "maintenance_mode": st.session_state.get("maintenance_mode_radio", "Scheduled Event Library (detailed PMI)"),
        "annual_escalation": float(st.session_state.get("annual_escalation_input", 5.0)),
        "planning_start_date": str(st.session_state.get("planning_start_date_input")),
        "use_custom_ac_dates": bool(st.session_state.get("use_custom_ac_dates_checkbox", False)),
        "years": int(st.session_state.get("planning_horizon_years", 1)),
        "target_availability": float(st.session_state.get("target_availability_input", 75.0)),
        "labour_rate": float(st.session_state.get("labour_rate_input", 115.0)),
        "labour_cost": float(st.session_state.get("labour_cost_input", 45.0)),
        "mgmt_fee_usd": float(st.session_state.get("mgmt_fee_usd", 10000.0)),
        "geographic_contingency_pct": float(st.session_state.get("geographic_contingency_pct_input", 0.0)),
    }

    fleet_size = payload["fleet_size"]
    payload["hours_until_pmi"] = [int(st.session_state.get(f"ac_{i+1}_pmi", 480)) for i in range(fleet_size)]
    payload["custom_ac_dates"] = [str(st.session_state.get(f"ac_{i+1}_start_date", payload["planning_start_date"])) for i in range(fleet_size)]
    for overhead_key in MRO_OVERHEAD_KEYS:
        payload[overhead_key] = float(st.session_state.get(overhead_key, 0.0))
    return payload


def _reset_sidebar_defaults():
    today = pd.Timestamp.now().normalize().date()
    st.session_state["currency_select"] = "USD"
    st.session_state["use_live_fx_toggle"] = False
    st.session_state["fleet_size_input"] = 1
    st.session_state["annual_hours_input"] = 300
    st.session_state["annual_escalation_input"] = 5.0
    st.session_state["planning_start_date_input"] = today
    st.session_state["use_custom_ac_dates_checkbox"] = False
    st.session_state["planning_horizon_years"] = 3
    st.session_state["mgmt_fee_usd"] = 10000.0
    st.session_state["ac_1_pmi"] = 480
    st.session_state["ac_1_start_date"] = today

    # Reset any dynamically created aircraft-specific fields from prior larger fleets.
    for key in list(st.session_state.keys()):
        if re.match(r"^ac_\d+_pmi$", key) and key != "ac_1_pmi":
            del st.session_state[key]
        if re.match(r"^ac_\d+_start_date$", key) and key != "ac_1_start_date":
            del st.session_state[key]

def show_sidebar():
    st.sidebar.title("UH-60 – Assumptions")

    pending_selection = st.session_state.pop("_pending_post_save_selection", None)
    if pending_selection:
        # Must be set before corresponding widgets are instantiated in this run.
        st.session_state["scenario_name_input"] = pending_selection
        st.session_state["saved_scenario_select"] = pending_selection

    pending_save_success = st.session_state.pop("_pending_save_success", None)
    if pending_save_success:
        st.sidebar.success(f"Saved scenario: {pending_save_success}")

    if st.sidebar.button("Reset to Defaults", use_container_width=True):
        _reset_sidebar_defaults()
        st.sidebar.success("Defaults restored.")
        st.rerun()

    # Scenario manager
    scenarios_path = _scenario_folder()
    scenario_files = sorted([p.stem for p in scenarios_path.glob("*.json")])

    if "scenario_name_input" not in st.session_state:
        st.session_state["scenario_name_input"] = "scenario_1"
    if "scenario_last_loaded" not in st.session_state:
        st.session_state["scenario_last_loaded"] = "None"

    st.sidebar.markdown("**Scenario Manager**")
    st.sidebar.caption(f"Last loaded: {st.session_state['scenario_last_loaded']}")
    st.sidebar.text_input("Scenario name", key="scenario_name_input")

    selected_scenario = st.sidebar.selectbox(
        "Saved scenarios",
        ["(none)"] + scenario_files,
        key="saved_scenario_select"
    )
    col_load, col_save = st.sidebar.columns(2)
    with col_load:
        if st.button("Load", key="load_selected_scenario"):
            if selected_scenario != "(none)":
                try:
                    scenario_data = json.loads((scenarios_path / f"{selected_scenario}.json").read_text(encoding="utf-8"))
                    _apply_loaded_scenario(scenario_data)
                    st.success(f"Loaded scenario: {selected_scenario}")
                    st.rerun()
                except Exception:
                    st.error("Unable to load selected scenario file.")
            else:
                st.warning("Select a saved scenario first.")
    with col_save:
        if st.button("Save", key="save_named_scenario"):
            typed_name = _sanitize_scenario_name(st.session_state.get("scenario_name_input", ""))
            target_scenario = typed_name if typed_name else (selected_scenario if selected_scenario != "(none)" else "")
            if not target_scenario:
                st.warning("Enter a valid scenario name.")
            else:
                # Save is executed after all sidebar widgets are evaluated so current values are persisted.
                st.session_state["_pending_save_scenario_name"] = target_scenario

    typed_name_preview = _sanitize_scenario_name(st.session_state.get("scenario_name_input", ""))
    resolved_save_target = typed_name_preview if typed_name_preview else (selected_scenario if selected_scenario != "(none)" else "(none)")
    st.sidebar.caption(f"Save target: {resolved_save_target}")

    uploaded_scenario = st.sidebar.file_uploader("Upload scenario JSON", type=["json"], key="scenario_upload")
    if st.sidebar.button("Load Uploaded", key="load_uploaded_scenario"):
        if uploaded_scenario is None:
            st.warning("Choose a JSON file to upload.")
        else:
            try:
                uploaded_data = json.loads(uploaded_scenario.getvalue().decode("utf-8"))
                _apply_loaded_scenario(uploaded_data)
                st.success("Uploaded scenario loaded.")
                st.rerun()
            except Exception:
                st.error("Uploaded file is not a valid scenario JSON.")

    st.sidebar.markdown("---")

    # Ensure stable defaults for all keyed widgets.
    st.session_state.setdefault("currency_select", "USD")
    st.session_state.setdefault("fleet_size_input", 1)
    st.session_state.setdefault("annual_hours_input", 600)
    st.session_state.setdefault("maintenance_mode_radio", "Scheduled Event Library (detailed PMI)")
    st.session_state.setdefault("annual_escalation_input", 5.0)
    st.session_state.setdefault("planning_start_date_input", pd.Timestamp.now().normalize().date())
    st.session_state.setdefault("use_custom_ac_dates_checkbox", False)
    st.session_state.setdefault("planning_horizon_years", 1)
    st.session_state.setdefault("target_availability_input", 75.0)
    st.session_state.setdefault("labour_rate_input", 115.0)
    st.session_state.setdefault("labour_cost_input", 45.0)
    st.session_state.setdefault("use_live_fx_toggle", False)
    st.session_state.setdefault("geographic_contingency_pct_input", 0.0)

    # Currency selection
    st.sidebar.markdown("**Currency Settings**")
    currency_options = {
        "USD": {"symbol": "$", "rate_to_usd": 1.0},
        "GBP": {"symbol": "£", "rate_to_usd": 1.27},
        "EUR": {"symbol": "€", "rate_to_usd": 1.09}
    }
    use_live_fx = st.sidebar.toggle("Use live FX rate", key="use_live_fx_toggle")
    currency = st.sidebar.selectbox("Display Currency", list(currency_options.keys()), key="currency_select")
    currency_symbol = currency_options[currency]["symbol"]
    static_currency_rate = currency_options[currency]["rate_to_usd"]
    currency_rate = static_currency_rate
    fx_source = "Static model assumption"
    fx_timestamp = "N/A"
    live_fx_available = False
    if use_live_fx:
        try:
            usd_to_currency_rate, fx_timestamp, fx_source = get_live_fx_rate("USD", currency)
            # Keep `currency_rate` semantics aligned with static values: 1 <currency> = X USD.
            # Frankfurter call above returns USD->currency, so invert it.
            if usd_to_currency_rate > 0:
                currency_rate = 1 / usd_to_currency_rate
            else:
                currency_rate = static_currency_rate
                fx_source = "Static model assumption (live API returned invalid rate)"
            live_fx_available = True
        except Exception:
            currency_rate = static_currency_rate
            fx_source = "Static model assumption (live API unavailable)"
            fx_timestamp = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M:%S")
    conversion_factor = 1 / currency_rate
    st.sidebar.markdown("*All cost inputs and calculations use USD. Display currency only converts outputs.*")
    st.sidebar.caption(
        f"Exchange rate used vs USD: 1 USD = {conversion_factor:.4f} {currency} | "
        f"1 {currency} = {currency_rate:.4f} USD"
    )
    st.sidebar.caption(f"Source: {fx_source}")
    st.sidebar.caption(f"Rate timestamp: {fx_timestamp}")
    if use_live_fx and not live_fx_available:
        st.sidebar.warning("Live FX lookup failed. Using static fallback rate.")

    # Fleet & utilisation
    fleet_size = st.sidebar.number_input("Fleet size (aircraft)", min_value=1, max_value=100, key="fleet_size_input")
    annual_hours_per_ac = st.sidebar.number_input(
        "Annual flight hours per aircraft", min_value=100, max_value=3000, step=50, key="annual_hours_input"
    )

    # Ensure newly active aircraft PMI fields start at 480, and heal stale zero defaults from prior sessions.
    skip_pmi_init = bool(st.session_state.pop("_skip_pmi_default_init_once", False))
    if not skip_pmi_init:
        previous_fleet_size = int(st.session_state.get("_last_fleet_size_for_pmi_defaults", 0))
        if previous_fleet_size != int(fleet_size):
            for i in range(int(fleet_size)):
                key = f"ac_{i+1}_pmi"
                current_val = st.session_state.get(key)
                if current_val is None or current_val == 0:
                    st.session_state[key] = 480
        st.session_state["_last_fleet_size_for_pmi_defaults"] = int(fleet_size)

    # Maintenance approach
    st.sidebar.subheader("Maintenance approach")
    maintenance_mode = st.sidebar.radio(
        "Maintenance approach",
        (
            "Parts Supply Only",
            "Scheduled Event Library (detailed PMI)",
            "Scheduled & Unscheduled Event Library (all events)"
        ),
        key="maintenance_mode_radio"
    )
    use_event_table = maintenance_mode == "Scheduled Event Library (detailed PMI)"
    use_unsched_event_library = maintenance_mode == "Scheduled & Unscheduled Event Library (all events)"


    # Cost Escalation
    st.sidebar.subheader("Cost Escalation")
    annual_escalation = st.sidebar.number_input(
        "Annual escalation rate (%)", min_value=0.0, max_value=20.0, step=0.5, key="annual_escalation_input"
    )

    st.sidebar.subheader("Geographic Contingency")
    geographic_contingency_pct = st.sidebar.slider(
        "Geographic contingency (%)",
        min_value=0.0,
        max_value=50.0,
        step=0.5,
        key="geographic_contingency_pct_input"
    )
    st.sidebar.caption("Applied to customer-facing manpower, parts, and MRO operating overhead costs.")



    # Aircraft fleet roster
    st.sidebar.subheader("Aircraft Fleet Roster")
    planning_start_date = st.sidebar.date_input(
        "Planning period start date",
        key="planning_start_date_input",
        format="DD/MM/YYYY"
    )
    use_custom_ac_dates = st.sidebar.checkbox(
        "Use custom start dates for individual aircraft", key="use_custom_ac_dates_checkbox"
    )

    custom_ac_dates = []
    if use_custom_ac_dates:
        auto_stagger_dates = fleet_size > 2
        if auto_stagger_dates:
            st.sidebar.caption("Custom start dates are auto-staggered in aircraft pairs at 3-month increments from planning start date.")
        for i in range(fleet_size):
            widget_key = f"ac_{i+1}_start_date"
            if auto_stagger_dates:
                st.session_state[widget_key] = (pd.Timestamp(planning_start_date) + pd.DateOffset(months=3 * (i // 2))).date()
            ac_date = st.sidebar.date_input(
                f"Aircraft {i+1} start date",
                key=widget_key,
                format="DD/MM/YYYY",
                disabled=auto_stagger_dates,
            )
            custom_ac_dates.append(ac_date)
    else:
        # Always fill with planning_start_date if not using custom
        custom_ac_dates = [planning_start_date for _ in range(fleet_size)]

    # Validation: ensure custom_ac_dates always matches fleet_size
    if len(custom_ac_dates) != fleet_size:
        st.warning("Custom aircraft start dates list does not match fleet size. Please check sidebar inputs.")

    # Hours until next PMI for each aircraft
    st.sidebar.subheader("Hours Until Next PMI (per aircraft)")
    hours_until_pmi = []
    for i in range(fleet_size):
        st.session_state.setdefault(f"ac_{i+1}_pmi", 480)
        hours = st.sidebar.number_input(
            f"Aircraft {i+1} hours until next PMI", min_value=0, max_value=480, key=f"ac_{i+1}_pmi"
        )
        hours_until_pmi.append(hours)


    # Planning horizon
    years = st.sidebar.slider("Planning horizon (years)", min_value=1, max_value=10, key="planning_horizon_years")

    # Target Availability
    st.sidebar.subheader("Target Availability")
    target_availability = st.sidebar.number_input(
        "Target Availability (%)", min_value=0.0, max_value=100.0, step=1.0, key="target_availability_input"
    )

    # Labour Rate
    st.sidebar.subheader("Labour Rate")
    labour_rate = st.sidebar.number_input(
        "Labour Rate per hour (USD)", min_value=0.0, step=1.0, key="labour_rate_input"
    )

    # Labour Cost
    labour_cost = st.sidebar.number_input(
        "Labour Cost per hour (USD)", min_value=0.0, step=1.0, key="labour_cost_input"
    )


    # Management fee
    if 'mgmt_fee_usd' not in st.session_state:
        st.session_state.mgmt_fee_usd = 10000.0
    annual_management_fee_per_ac = st.sidebar.slider(
        "Annual management fee per aircraft (USD)",
        0,
        100000,
        int(st.session_state.mgmt_fee_usd),
        1000,
        key="annual_management_fee_input"
    )
    st.session_state.mgmt_fee_usd = float(annual_management_fee_per_ac)

    pending_save_name = st.session_state.pop("_pending_save_scenario_name", None)
    if pending_save_name:
        payload = _collect_scenario_payload()
        payload["name"] = pending_save_name
        (scenarios_path / f"{pending_save_name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        st.session_state["_pending_post_save_selection"] = pending_save_name
        st.session_state["_pending_save_success"] = pending_save_name
        st.rerun()

    # Return all sidebar values as a dict
    return {
        "currency": currency,
        "currency_symbol": currency_symbol,
        "conversion_factor": conversion_factor,
        "use_live_fx": use_live_fx,
        "fx_source": fx_source,
        "fx_timestamp": fx_timestamp,
        "rate_to_usd": currency_rate,
        "fleet_size": fleet_size,
        "annual_hours_per_ac": annual_hours_per_ac,
        "maintenance_mode": maintenance_mode,
        "use_event_table": use_event_table,
        "use_unsched_event_library": use_unsched_event_library,
        "annual_escalation": annual_escalation,
        "geographic_contingency_pct": geographic_contingency_pct,
        "geographic_contingency_multiplier": 1 + (geographic_contingency_pct / 100.0),
        "planning_start_date": planning_start_date,
        "use_custom_ac_dates": use_custom_ac_dates,
        "custom_ac_dates": custom_ac_dates,
        "years": years,
        "annual_management_fee_per_ac": annual_management_fee_per_ac,
        "hours_until_pmi": hours_until_pmi,
        "target_availability": target_availability,
        "labour_rate": labour_rate,
        "labour_cost": labour_cost
    }
