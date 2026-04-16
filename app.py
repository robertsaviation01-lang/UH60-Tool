"""
UH-60 Maintenance Modeling App (Streamlit)
Entry point. UI logic only. Calls into core, data, and ui layers.
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import os
import shutil
import base64
import hmac
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
from urllib.parse import quote
import math

# Import sidebar component
from ui.sidebar import show_sidebar

st.set_page_config(page_title="UH-60 Maintenance Model", layout="wide")

APP_MODEL_VERSION = "v1.1.0"


def _get_app_password():
	try:
		secret_password = st.secrets.get("APP_PASSWORD", "")
	except Exception:
		secret_password = ""
	if secret_password:
		return str(secret_password)
	return str(os.getenv("APP_PASSWORD", ""))


def _require_password():
	configured_password = _get_app_password()
	if not configured_password:
		st.error("Access control is enabled, but no app password is configured.")
		st.info("Set APP_PASSWORD in Streamlit secrets or as an environment variable.")
		st.stop()

	if st.session_state.get("authenticated", False):
		if st.sidebar.button("Log out", key="logout_button"):
			st.session_state["authenticated"] = False
			st.rerun()
		return

	st.title("UH-60 Maintenance Modelling App")
	st.caption(f"Model Version: {APP_MODEL_VERSION}")
	st.subheader("Restricted Access")
	st.write("Enter the app password to continue.")

	entered_password = st.text_input("Password", type="password", key="password_gate_input")
	if st.button("Sign in", key="password_gate_submit"):
		if hmac.compare_digest(entered_password, configured_password):
			st.session_state["authenticated"] = True
			st.rerun()
		st.error("Incorrect password.")

	st.stop()


_require_password()

st.title("UH-60 Maintenance Modelling App")
st.caption(f"Model Version: {APP_MODEL_VERSION}")
st.write("Welcome! This is the entry point for the UH-60 maintenance modeling tool.")

# Show sidebar and get all sidebar values

sidebar_values = show_sidebar()

# Carry session-scoped MRO operating costs into the main model values dict.
for _overhead_key in [
	"mro_cost_insurance",
	"mro_cost_facility",
	"mro_cost_gse",
	"mro_cost_tooling",
	"mro_cost_engine_bay",
	"mro_cost_rotables_store",
	"mro_cost_parts_store",
	"mro_cost_utilities",
	"mro_cost_it_quality",
]:
	sidebar_values[_overhead_key] = float(st.session_state.get(_overhead_key, 0.0))

st.sidebar.caption(f"Model Version: {APP_MODEL_VERSION}")


def _format_currency(symbol, value, decimals=0):
	return f"{symbol}{value:,.{decimals}f}"


def _display_conversion_factor(values):
	try:
		factor = float(values.get("conversion_factor", 1.0))
		return factor if factor > 0 else 1.0
	except Exception:
		return 1.0


def _plotly_export_config(filename):
	return {
		"displaylogo": False,
		"toImageButtonOptions": {
			"format": "png",
			"filename": filename,
			"scale": 2,
		},
	}


def _parse_downtime_days(value):
	if pd.isna(value):
		return 0.0
	text = str(value).strip().lower()
	if not text:
		return 0.0
	try:
		return float(text.split()[0])
	except Exception:
		try:
			return float(text)
		except Exception:
			return 0.0


def _get_aircraft_start(values, ac_idx, planning_start_date):
	if values.get("use_custom_ac_dates") and int(values.get("fleet_size", 1)) > 2:
		return planning_start_date + pd.DateOffset(months=3 * (ac_idx // 2))
	if values.get("use_custom_ac_dates") and len(values.get("custom_ac_dates", [])) > ac_idx:
		return pd.to_datetime(values["custom_ac_dates"][ac_idx])
	return planning_start_date


def _is_parts_supply_only_mode(mode_value):
	mode = str(mode_value or "").strip().lower()
	return mode in {
		"parts supply only",
		"part only",
		"parts only",
		"manual mode",
	}


def _is_unscheduled_library_mode(mode_value):
	mode = str(mode_value or "").strip().lower()
	return mode in {
		"scheduled & unscheduled maintenance",
		"scheduled and unscheduled maintenance",
		"scheduled and unscheduled event library",
		"scheduled & unscheduled event library",
		"scheduled & unscheduled event library (all events)",
	}


def _expected_unscheduled_events(flight_hours):
	return max(float(flight_hours or 0.0), 0.0) / 300.0


def _mro_overhead_categories():
	return [
		("Insurance", "mro_cost_insurance"),
		("Facility Costs", "mro_cost_facility"),
		("GSE", "mro_cost_gse"),
		("UH-60 Specialist Tooling", "mro_cost_tooling"),
		("Engine Bay", "mro_cost_engine_bay"),
		("Rotables Store", "mro_cost_rotables_store"),
		("Parts Store", "mro_cost_parts_store"),
		("Utilities", "mro_cost_utilities"),
		("IT/Quality/Compliance", "mro_cost_it_quality"),
	]


def _annual_mro_overheads_from_values(values):
	annual_costs = {}
	for label, key in _mro_overhead_categories():
		annual_costs[label] = float(values.get(key, 0.0))
	annual_total = sum(annual_costs.values())
	return annual_costs, annual_total


def _count_events_for_ac_report(ac_idx, values, scheduled_df):
	planning_start_date = pd.to_datetime(values["planning_start_date"])
	ac_start_date = _get_aircraft_start(values, ac_idx, planning_start_date)
	contract_end_date = ac_start_date + pd.DateOffset(years=values["years"])
	annual_hours = float(values["annual_hours_per_ac"])
	hours_until_next_pmi = float(values["hours_until_pmi"][ac_idx])
	contract_fh = float(values["years"]) * annual_hours
	fh_tolerance = 1e-6

	raw_event_types = scheduled_df["Scheduled Event"].dropna().unique().tolist() if not scheduled_df.empty else []
	normalized_event_types = [e.strip().lower() for e in raw_event_types if e not in ("Daily Pre-flight", "")]
	summary = {e: 0 for e in normalized_event_types}

	pmi_cycle = ["pmi 1", "pmi 2"]
	pmi_idx = 0
	next_pmi_fh = hours_until_next_pmi if hours_until_next_pmi > 0 else 480.0
	while next_pmi_fh <= contract_fh + fh_tolerance:
		event = pmi_cycle[pmi_idx % 2]
		if event in summary:
			summary[event] += 1
		next_pmi_fh += 480
		pmi_idx += 1

	for _, row in scheduled_df.iterrows():
		event = row.get("Scheduled Event", "")
		if not isinstance(event, str):
			continue
		event_key = event.strip().lower()
		if event_key in ("pmi 1", "pmi 2", "daily pre-flight", ""):
			continue
		if event_key == "90-day corrosion check":
			interval_days = 90
		elif event_key == "6-month insp":
			interval_days = 182
		elif event_key == "annual insp":
			interval_days = 365
		else:
			interval_val = row.get("Interval (hrs)")
			interval_days = None
			interval = float(interval_val) if pd.notna(interval_val) and interval_val != "" else None

		if interval_days is not None:
			n_events = int((contract_end_date - ac_start_date).days // interval_days)
			summary[event_key] += max(n_events, 0)
		elif interval is not None and interval > 0:
			fh_pointer = 0.0
			while True:
				fh_pointer += interval
				if fh_pointer > contract_fh + fh_tolerance:
					break
				summary[event_key] += 1

	return summary


@st.cache_data(show_spinner=False)
def _build_costings_dataframe(values, apply_escalation=True):
	planning_start_date = pd.to_datetime(values["planning_start_date"])
	n_years = int(values["years"])
	planning_end_date = planning_start_date + pd.DateOffset(years=n_years)
	annual_hours = float(values["annual_hours_per_ac"])
	fleet_size = int(values["fleet_size"])
	labour_rate = float(values.get("labour_rate", 0.0))
	escalation_rate = float(values.get("annual_escalation", 0.0)) / 100.0
	contingency_multiplier = 1.0 + (float(values.get("geographic_contingency_pct", 0.0)) / 100.0)
	annual_management_fee = float(values.get("annual_management_fee_per_ac", 0.0)) * fleet_size
	overhead_annual_by_category, _ = _annual_mro_overheads_from_values(values)

	year_windows = [
		(planning_start_date + pd.DateOffset(years=y), planning_start_date + pd.DateOffset(years=y + 1))
		for y in range(n_years)
	]

	def year_index_for_date(dt):
		for y, (y_start, y_end) in enumerate(year_windows):
			if y_start <= dt < y_end:
				return y
		if year_windows and dt == year_windows[-1][1]:
			return len(year_windows) - 1
		return None

	rows = []
	for y, (y_start, y_end) in enumerate(year_windows):
		esc = (1 + escalation_rate) ** y if apply_escalation else 1.0
		overhead_values = {
			label: amount * esc * contingency_multiplier
			for label, amount in overhead_annual_by_category.items()
		}
		rows.append({
			"Contract Year": y + 1,
			"Period": f"{y_start.strftime('%d/%m/%Y')} - {(y_end - pd.Timedelta(days=1)).strftime('%d/%m/%Y')}",
			"Total FH": 0.0,
			"Manpower Hrs": 0.0,
			"Manpower Cost": 0.0,
			"Parts Cost": 0.0,
			"Management Fee": annual_management_fee * esc,
			**overhead_values,
			"MRO Overheads": sum(overhead_values.values()),
		})

	for ac_idx in range(fleet_size):
		ac_start = _get_aircraft_start(values, ac_idx, planning_start_date)
		ac_contract_end = ac_start + pd.DateOffset(years=n_years)
		ac_active_start = max(ac_start, planning_start_date)
		ac_active_end = min(ac_contract_end, planning_end_date)
		if ac_active_end <= ac_active_start:
			continue
		for y, (y_start, y_end) in enumerate(year_windows):
			overlap_start = max(ac_active_start, y_start)
			overlap_end = min(ac_active_end, y_end)
			if overlap_end > overlap_start:
				days = (overlap_end - overlap_start).days
				year_days = max((y_end - y_start).days, 1)
				rows[y]["Total FH"] += annual_hours * (days / year_days)

	scheduled_path = os.path.join("data", "scheduled_events.csv")
	if os.path.exists(scheduled_path):
		scheduled_df = pd.read_csv(scheduled_path)
	else:
		scheduled_df = pd.DataFrame()

	event_cost_lookup = {}
	for _, row in scheduled_df.iterrows():
		event = row.get("Scheduled Event", "")
		if not isinstance(event, str):
			continue
		event_key = event.strip().lower()
		event_cost_lookup[event_key] = {
			"mh": float(row["Man-Hours"]) if pd.notna(row.get("Man-Hours")) else 0.0,
			"parts": float(row["Parts $ / event"]) if pd.notna(row.get("Parts $ / event")) else 0.0,
		}

	def add_event_cost(event_date, man_hours, parts_cost, ac_start, ac_end):
		if event_date < planning_start_date or event_date > planning_end_date:
			return
		if event_date < ac_start or event_date > ac_end:
			return
		y = year_index_for_date(event_date)
		if y is None:
			return
		esc = (1 + escalation_rate) ** y if apply_escalation else 1.0
		is_manual_mode = _is_parts_supply_only_mode(values.get("maintenance_mode"))
		if not is_manual_mode:
			rows[y]["Manpower Hrs"] += man_hours
			rows[y]["Manpower Cost"] += man_hours * labour_rate * esc * contingency_multiplier
		rows[y]["Parts Cost"] += parts_cost * esc * contingency_multiplier

	for ac_idx in range(fleet_size):
		ac_start = _get_aircraft_start(values, ac_idx, planning_start_date)
		ac_end = min(ac_start + pd.DateOffset(years=n_years), planning_end_date)
		if ac_end <= ac_start:
			continue

		fh0 = float(values["hours_until_pmi"][ac_idx])
		active_days = (ac_end - ac_start).days
		full_contract_days = max((ac_start + pd.DateOffset(years=n_years) - ac_start).days, 1)
		contract_fraction = active_days / full_contract_days
		contract_fh = float(n_years) * annual_hours * contract_fraction
		fh_tolerance = 1.0

		pmi_cycle = ["pmi 1", "pmi 2"]
		pmi_idx = 0
		first_pmi_offset = fh0 if fh0 > 0 else 480.0
		next_pmi_fh = first_pmi_offset
		contract_fh_end = contract_fh
		while next_pmi_fh <= contract_fh_end + fh_tolerance:
			event_key = pmi_cycle[pmi_idx % 2]
			years_since_start = next_pmi_fh / annual_hours if annual_hours > 0 else 0
			event_date = ac_start + pd.DateOffset(days=int(years_since_start * 365.25))
			cost_vals = event_cost_lookup.get(event_key, {"mh": 0.0, "parts": 0.0})
			add_event_cost(event_date, cost_vals["mh"], cost_vals["parts"], ac_start, ac_end)
			next_pmi_fh += 480
			pmi_idx += 1

		for _, row in scheduled_df.iterrows():
			event = row.get("Scheduled Event", "")
			if not isinstance(event, str):
				continue
			event_key = event.strip().lower()
			if event_key in ("pmi 1", "pmi 2", "daily pre-flight", ""):
				continue

			if event_key == "90-day corrosion check":
				interval_days = 90
				next_date = ac_start + pd.Timedelta(days=interval_days)
				while next_date <= ac_end:
					cost_vals = event_cost_lookup.get(event_key, {"mh": 0.0, "parts": 0.0})
					add_event_cost(next_date, cost_vals["mh"], cost_vals["parts"], ac_start, ac_end)
					next_date += pd.Timedelta(days=interval_days)
			elif event_key == "6-month insp":
				interval_days = 182
				next_date = ac_start + pd.Timedelta(days=interval_days)
				while next_date <= ac_end:
					cost_vals = event_cost_lookup.get(event_key, {"mh": 0.0, "parts": 0.0})
					add_event_cost(next_date, cost_vals["mh"], cost_vals["parts"], ac_start, ac_end)
					next_date += pd.Timedelta(days=interval_days)
			elif event_key == "annual insp":
				interval_days = 365
				next_date = ac_start + pd.Timedelta(days=interval_days)
				while next_date <= ac_end:
					cost_vals = event_cost_lookup.get(event_key, {"mh": 0.0, "parts": 0.0})
					add_event_cost(next_date, cost_vals["mh"], cost_vals["parts"], ac_start, ac_end)
					next_date += pd.Timedelta(days=interval_days)
			else:
				interval_val = row.get("Interval (hrs)")
				if pd.notna(interval_val) and interval_val != "":
					interval = float(interval_val)
				else:
					interval = None
				if interval is not None and interval > 0:
					next_event_fh = 0.0
					while True:
						next_event_fh += interval
						if next_event_fh > contract_fh_end + fh_tolerance:
							break
						years_since_start = next_event_fh / annual_hours if annual_hours > 0 else 0
						event_date = ac_start + pd.DateOffset(days=int(years_since_start * 365.25))
						cost_vals = event_cost_lookup.get(event_key, {"mh": 0.0, "parts": 0.0})
						add_event_cost(event_date, cost_vals["mh"], cost_vals["parts"], ac_start, ac_end)

		if _is_unscheduled_library_mode(values.get("maintenance_mode")):
			unsched_path = os.path.join("data", "unscheduled_events.csv")
			if os.path.exists(unsched_path):
				unsched_df = pd.read_csv(unsched_path)
				for y, (y_start, y_end) in enumerate(year_windows):
					overlap_start = max(ac_start, y_start, planning_start_date)
					overlap_end = min(ac_end, y_end, planning_end_date)
					if overlap_end <= overlap_start:
						continue
					year_days = max((y_end - y_start).days, 1)
					overlap_days = (overlap_end - overlap_start).days
					year_fh = annual_hours * (overlap_days / year_days)
					expected_events = _expected_unscheduled_events(year_fh)
					if expected_events <= 0:
						continue
					esc = (1 + escalation_rate) ** y if apply_escalation else 1.0
					is_manual_mode = _is_parts_supply_only_mode(values.get("maintenance_mode"))
					for _, row in unsched_df.iterrows():
						mh = float(row["Avg. Labour Hours"]) if pd.notna(row.get("Avg. Labour Hours")) else 0.0
						parts = float(row["Adjusted Parts Cost"]) if pd.notna(row.get("Adjusted Parts Cost")) else 0.0
						if not is_manual_mode:
							rows[y]["Manpower Hrs"] += mh * expected_events
							rows[y]["Manpower Cost"] += mh * expected_events * labour_rate * esc * contingency_multiplier
						rows[y]["Parts Cost"] += parts * expected_events * esc * contingency_multiplier

	costings_df = pd.DataFrame(rows)
	costings_df["Total Cost"] = (
		costings_df["Manpower Cost"]
		+ costings_df["Parts Cost"]
		+ costings_df["Management Fee"]
		+ costings_df["MRO Overheads"]
	)
	return costings_df


@st.cache_data(show_spinner=False)
def _build_maintenance_schedule_figure(values):
	scheduled_path = os.path.join("data", "scheduled_events.csv")
	if not os.path.exists(scheduled_path):
		return None
	scheduled_df = pd.read_csv(scheduled_path)
	if scheduled_df.empty:
		return None

	fleet_size = int(values.get("fleet_size", 0))
	annual_hours = float(values.get("annual_hours_per_ac", 0.0))
	contract_years = int(values.get("years", 0))

	summary = {}
	for ac_idx in range(fleet_size):
		ac_summary = _count_events_for_ac_report(ac_idx, values, scheduled_df)
		for event_name, count in ac_summary.items():
			summary[event_name] = summary.get(event_name, 0) + count

	if _is_unscheduled_library_mode(values.get("maintenance_mode")):
		unsched_path = os.path.join("data", "unscheduled_events.csv")
		if os.path.exists(unsched_path):
			unsched_df = pd.read_csv(unsched_path)
			for _, row in unsched_df.iterrows():
				event = row.get("Unscheduled Event")
				if isinstance(event, str):
					summary[event] = _expected_unscheduled_events(annual_hours) * contract_years * fleet_size

	plot_df = pd.DataFrame([
		{"Event": k, "Total Events": v}
		for k, v in summary.items()
		if v > 0
	])
	if plot_df.empty:
		return None

	plot_df = plot_df.sort_values("Total Events", ascending=False)
	fig = px.bar(
		plot_df,
		x="Event",
		y="Total Events",
		title="Maintenance Schedule: Event Totals (Fleet)",
		labels={"Event": "Event", "Total Events": "Total Events"},
	)
	fig.update_layout(xaxis_tickangle=-30)
	return fig


@st.cache_data(show_spinner=False)
def _build_maintenance_timeline_figure(values):
	scheduled_path = os.path.join("data", "scheduled_events.csv")
	if not os.path.exists(scheduled_path):
		return None
	scheduled_df = pd.read_csv(scheduled_path)
	if scheduled_df.empty:
		return None

	planning_start_date = pd.to_datetime(values["planning_start_date"])
	n_years = int(values["years"])
	planning_end_date = planning_start_date + pd.DateOffset(years=n_years)
	annual_hours = float(values.get("annual_hours_per_ac", 0.0))
	fleet_size = int(values.get("fleet_size", 0))

	records = []

	def add_record(event_date, mh, event_type, ac_start, ac_end):
		if event_date < planning_start_date or event_date >= planning_end_date:
			return
		if event_date < ac_start or event_date >= ac_end:
			return
		month_idx = (event_date.year - planning_start_date.year) * 12 + (event_date.month - planning_start_date.month)
		records.append({"Month": month_idx, "Type": event_type, "Manpower": float(mh)})

	for ac_idx in range(fleet_size):
		ac_start = _get_aircraft_start(values, ac_idx, planning_start_date)
		ac_end = min(ac_start + pd.DateOffset(years=n_years), planning_end_date)
		if ac_end <= ac_start:
			continue

		fh0 = float(values["hours_until_pmi"][ac_idx])
		contract_duration_years = (ac_end - ac_start).days / 365.25
		contract_fh_end = contract_duration_years * annual_hours
		fh_tolerance = 1.0

		pmi_cycle = ["pmi 1", "pmi 2"]
		pmi_idx = 0
		next_pmi_fh = fh0 if fh0 > 0 else 480.0
		while next_pmi_fh <= contract_fh_end + fh_tolerance:
			event_key = pmi_cycle[pmi_idx % 2]
			years_since_start = next_pmi_fh / annual_hours if annual_hours > 0 else 0
			event_date = ac_start + pd.DateOffset(days=int(years_since_start * 365.25))
			mh_vals = scheduled_df.loc[
				scheduled_df["Scheduled Event"].astype(str).str.strip().str.lower() == event_key,
				"Man-Hours",
			].values
			mh = float(mh_vals[0]) if len(mh_vals) > 0 and pd.notna(mh_vals[0]) else 0.0
			add_record(event_date, mh, "Scheduled", ac_start, ac_end)
			next_pmi_fh += 480
			pmi_idx += 1

		for _, row in scheduled_df.iterrows():
			event = row.get("Scheduled Event", "")
			if not isinstance(event, str):
				continue
			event_key = event.strip().lower()
			if event_key in ("pmi 1", "pmi 2", "daily pre-flight", ""):
				continue
			mh = float(row.get("Man-Hours")) if pd.notna(row.get("Man-Hours")) else 0.0

			if event_key == "90-day corrosion check":
				next_date = ac_start + pd.Timedelta(days=90)
				while next_date <= ac_end:
					add_record(next_date, mh, "Scheduled", ac_start, ac_end)
					next_date += pd.Timedelta(days=90)
			elif event_key == "6-month insp":
				next_date = ac_start + pd.Timedelta(days=182)
				while next_date <= ac_end:
					add_record(next_date, mh, "Scheduled", ac_start, ac_end)
					next_date += pd.Timedelta(days=182)
			elif event_key == "annual insp":
				next_date = ac_start + pd.Timedelta(days=365)
				while next_date <= ac_end:
					add_record(next_date, mh, "Scheduled", ac_start, ac_end)
					next_date += pd.Timedelta(days=365)
			else:
				interval_val = row.get("Interval (hrs)")
				if pd.notna(interval_val) and interval_val != "":
					interval = float(interval_val)
				else:
					interval = None
				if interval is not None and interval > 0:
					next_event_fh = 0.0
					while True:
						next_event_fh += interval
						if next_event_fh > contract_fh_end + fh_tolerance:
							break
						years_since_start = next_event_fh / annual_hours if annual_hours > 0 else 0
						event_date = ac_start + pd.DateOffset(days=int(years_since_start * 365.25))
						add_record(event_date, mh, "Scheduled", ac_start, ac_end)

		if _is_unscheduled_library_mode(values.get("maintenance_mode")):
			unsched_path = os.path.join("data", "unscheduled_events.csv")
			if os.path.exists(unsched_path):
				unsched_df = pd.read_csv(unsched_path)
				expected_events = _expected_unscheduled_events(contract_fh_end)
				for _, row in unsched_df.iterrows():
					mh = float(row.get("Avg. Labour Hours")) if pd.notna(row.get("Avg. Labour Hours")) else 0.0
					if expected_events > 0:
						event_date = ac_start + pd.DateOffset(days=int((ac_end - ac_start).days / 2))
						add_record(event_date, mh * expected_events, "Unscheduled", ac_start, ac_end)

	if not records:
		return None

	plot_df = pd.DataFrame(records).groupby(["Month", "Type"], as_index=False)["Manpower"].sum()
	total_months = max((planning_end_date.year - planning_start_date.year) * 12 + (planning_end_date.month - planning_start_date.month), 1)
	month_labels = [(planning_start_date + pd.DateOffset(months=int(m))).strftime("%m/%Y") for m in range(total_months)]
	month_map = {m: lbl for m, lbl in enumerate(month_labels)}
	plot_df["MonthLabel"] = plot_df["Month"].map(month_map)

	fig = px.bar(
		plot_df,
		x="MonthLabel",
		y="Manpower",
		color="Type",
		barmode="stack",
		title="Maintenance Timeline: Monthly Manpower (Fleet)",
		labels={"MonthLabel": "Month", "Manpower": "Manpower Hours"},
	)
	fig.update_layout(xaxis=dict(categoryorder="array", categoryarray=month_labels), xaxis_tickangle=-30)
	return fig


@st.cache_data(show_spinner=False)
def _build_costings_fh_cost_figure(values):
	try:
		costings_df = _build_costings_dataframe(values, apply_escalation=True)
	except Exception:
		return None

	contract_fh_total = float(costings_df["Total FH"].sum()) if "Total FH" in costings_df.columns else 0.0
	if contract_fh_total <= 0:
		return None

	display_factor = _display_conversion_factor(values)
	cs = values.get("currency_symbol", "$")
	component_items = ["Manpower Cost", "Parts Cost", "Management Fee", "MRO Overheads"]
	present_components = [item for item in component_items if item in costings_df.columns]
	if not present_components:
		return None

	component_values = [
		(float(costings_df[item].sum()) / contract_fh_total) * display_factor
		for item in present_components
	]
	line_x = present_components + ["Total Cost"]
	line_y = []
	running_total = 0.0
	for val in component_values:
		running_total += val
		line_y.append(running_total)
	line_y.append(running_total)

	fig = go.Figure()
	fig.add_trace(go.Bar(
		x=present_components,
		y=component_values,
		name="Cost Components",
		text=[f"{cs}{v:,.0f}" for v in component_values],
		textposition="outside",
	))
	fig.add_trace(go.Scatter(
		x=line_x,
		y=line_y,
		mode="lines+markers+text",
		name="Total Cost (Cumulative)",
		line={"width": 3},
		text=[f"{cs}{v:,.0f}" for v in line_y],
		textposition="top center",
	))
	fig.update_layout(
		title="Annual Costings by Contract Year: FH Cost by Costing Item",
		yaxis_tickformat=",.0f",
		xaxis_title="Costing Item",
		yaxis_title=f"FH Cost ({cs})",
	)
	return fig


@st.cache_data(show_spinner=False)
def _build_overheads_fh_cost_figure(values):
	try:
		costings_df = _build_costings_dataframe(values, apply_escalation=True)
	except Exception:
		return None

	contract_fh_total = float(costings_df["Total FH"].sum()) if "Total FH" in costings_df.columns else 0.0
	if contract_fh_total <= 0:
		return None

	overhead_cols_present = [label for label, _ in _mro_overhead_categories() if label in costings_df.columns]
	if not overhead_cols_present:
		return None

	display_factor = _display_conversion_factor(values)
	cs = values.get("currency_symbol", "$")
	overhead_component_values = [
		(float(costings_df[col].sum()) / contract_fh_total) * display_factor
		for col in overhead_cols_present
	]
	overhead_line_x = overhead_cols_present + ["MRO Overheads"]
	overhead_line_y = []
	overhead_running_total = 0.0
	for val in overhead_component_values:
		overhead_running_total += val
		overhead_line_y.append(overhead_running_total)
	overhead_line_y.append(overhead_running_total)

	fig = go.Figure()
	fig.add_trace(go.Bar(
		x=overhead_cols_present,
		y=overhead_component_values,
		name="Overhead Components",
		text=[f"{cs}{v:,.0f}" for v in overhead_component_values],
		textposition="outside",
	))
	fig.add_trace(go.Scatter(
		x=overhead_line_x,
		y=overhead_line_y,
		mode="lines+markers+text",
		name="MRO Overheads (Cumulative)",
		line={"width": 3},
		text=[f"{cs}{v:,.0f}" for v in overhead_line_y],
		textposition="top center",
	))
	fig.update_layout(
		title="Annual MRO Overheads Breakdown: FH Cost by Overhead Item",
		yaxis_tickformat=",.0f",
		xaxis_title="Overhead Item",
		yaxis_title=f"FH Cost ({cs})",
	)
	return fig


def _compute_mro_staffing(values):
	"""Derive MRO staffing estimates from model values dict. Returns a dict."""
	mp_productive_hrs = int(values.get("mp_productive_hrs", 1500))
	mp_engineer_ratio = int(values.get("mp_engineer_ratio", 3))
	mp_qc_ratio = int(values.get("mp_qc_ratio", 8))
	mp_parts_ratio = int(values.get("mp_parts_ratio", 4))
	mp_include_planning = bool(values.get("mp_include_planning", True))
	mp_shift_coverage = str(values.get("mp_shift_coverage", "Single shift (1×)"))
	shift_mult = 2.0 if mp_shift_coverage.startswith("Double") else 1.0
	fleet_size = int(values.get("fleet_size", 1))
	contract_years = int(values.get("years", 1))
	labour_rate = float(values.get("labour_rate", 0.0))
	labour_cost_rate = float(values.get("labour_cost", 0.0))
	contingency_multiplier = 1.0 + float(values.get("geographic_contingency_pct", 0.0)) / 100.0
	try:
		cdf = _build_costings_dataframe(values, apply_escalation=True)
		annual_avg_mh = float(cdf["Manpower Hrs"].sum()) / contract_years if contract_years > 0 else 0.0
		year_rows = cdf.to_dict("records")
	except Exception:
		annual_avg_mh = 0.0
		year_rows = []
	direct_staff_raw = (annual_avg_mh / mp_productive_hrs * shift_mult) if mp_productive_hrs > 0 else 0.0
	direct_staff = max(1, math.ceil(direct_staff_raw)) if annual_avg_mh > 0 else 0
	lae_count = max(1, math.ceil(direct_staff / (mp_engineer_ratio + 1))) if direct_staff > 0 else 0
	mechanic_count = max(0, direct_staff - lae_count)
	qc_count = max(1, math.ceil(direct_staff / mp_qc_ratio)) if direct_staff > 0 else 1
	parts_count = max(1, math.ceil(fleet_size / mp_parts_ratio))
	planning_count = 1 if mp_include_planning else 0
	total_staff = direct_staff + qc_count + parts_count + planning_count
	utilisation_pct = (
		(annual_avg_mh / (direct_staff * mp_productive_hrs) * 100.0)
		if direct_staff * mp_productive_hrs > 0 else 0.0
	)
	annual_staff_cost = total_staff * mp_productive_hrs * labour_cost_rate
	annual_charge = total_staff * mp_productive_hrs * labour_rate * contingency_multiplier
	per_year = []
	for yr in year_rows:
		yr_mh = float(yr.get("Manpower Hrs", 0.0))
		yr_direct_raw = (yr_mh / mp_productive_hrs * shift_mult) if mp_productive_hrs > 0 else 0.0
		yr_direct = max(1, math.ceil(yr_direct_raw)) if yr_mh > 0 else 0
		yr_lae = max(1, math.ceil(yr_direct / (mp_engineer_ratio + 1))) if yr_direct > 0 else 0
		yr_mech = max(0, yr_direct - yr_lae)
		yr_qc = max(1, math.ceil(yr_direct / mp_qc_ratio)) if yr_direct > 0 else 1
		yr_parts = max(1, math.ceil(fleet_size / mp_parts_ratio))
		yr_planning = planning_count
		yr_total = yr_direct + yr_qc + yr_parts + yr_planning
		yr_util = (yr_mh / (yr_direct * mp_productive_hrs) * 100.0) if yr_direct * mp_productive_hrs > 0 else 0.0
		yr_staff_cost = yr_total * mp_productive_hrs * labour_cost_rate
		yr_charge = yr_total * mp_productive_hrs * labour_rate * contingency_multiplier
		per_year.append({
			"year": int(yr.get("Contract Year", 0)),
			"period": str(yr.get("Period", "")),
			"mh": yr_mh,
			"lae": yr_lae,
			"mechanics": yr_mech,
			"qc": yr_qc,
			"parts": yr_parts,
			"planning": yr_planning,
			"total": yr_total,
			"util_pct": yr_util,
			"staff_cost": yr_staff_cost,
			"charge": yr_charge,
		})
	return {
		"mp_productive_hrs": mp_productive_hrs,
		"mp_engineer_ratio": mp_engineer_ratio,
		"mp_qc_ratio": mp_qc_ratio,
		"mp_parts_ratio": mp_parts_ratio,
		"mp_include_planning": mp_include_planning,
		"mp_shift_coverage": mp_shift_coverage,
		"lae_count": lae_count,
		"mechanic_count": mechanic_count,
		"qc_count": qc_count,
		"parts_count": parts_count,
		"planning_count": planning_count,
		"direct_staff": direct_staff,
		"total_staff": total_staff,
		"utilisation_pct": utilisation_pct,
		"annual_avg_mh": annual_avg_mh,
		"annual_staff_cost": annual_staff_cost,
		"annual_charge": annual_charge,
		"per_year": per_year,
	}


def _get_mro_level_presets():
	"""Return the three MRO capability level presets."""
	return {
		"Lean": {
			"mro_cost_insurance": {"fixed": 30000.0, "per_ac": 2500.0},
			"mro_cost_facility": {"fixed": 120000.0, "per_ac": 5000.0},
			"mro_cost_gse": {"fixed": 20000.0, "per_ac": 2100.0},
			"mro_cost_tooling": {"fixed": 40000.0, "per_ac": 2500.0},
			"mro_cost_engine_bay": {"fixed": 25000.0, "per_ac": 2500.0},
			"mro_cost_rotables_store": {"fixed": 12000.0, "per_ac": 1500.0},
			"mro_cost_parts_store": {"fixed": 16000.0, "per_ac": 2000.0},
			"mro_cost_utilities": {"fixed": 20000.0, "per_ac": 2500.0},
			"mro_cost_it_quality": {"fixed": 18000.0, "per_ac": 2250.0},
		},
		"Standard": {
			"mro_cost_insurance": {"fixed": 50000.0, "per_ac": 3333.0},
			"mro_cost_facility": {"fixed": 180000.0, "per_ac": 11667.0},
			"mro_cost_gse": {"fixed": 35000.0, "per_ac": 4167.0},
			"mro_cost_tooling": {"fixed": 80000.0, "per_ac": 5000.0},
			"mro_cost_engine_bay": {"fixed": 60000.0, "per_ac": 5000.0},
			"mro_cost_rotables_store": {"fixed": 30000.0, "per_ac": 3750.0},
			"mro_cost_parts_store": {"fixed": 35000.0, "per_ac": 5000.0},
			"mro_cost_utilities": {"fixed": 40000.0, "per_ac": 5000.0},
			"mro_cost_it_quality": {"fixed": 38000.0, "per_ac": 3500.0},
		},
		"Full": {
			"mro_cost_insurance": {"fixed": 70000.0, "per_ac": 5000.0},
			"mro_cost_facility": {"fixed": 260000.0, "per_ac": 20000.0},
			"mro_cost_gse": {"fixed": 60000.0, "per_ac": 7500.0},
			"mro_cost_tooling": {"fixed": 140000.0, "per_ac": 10000.0},
			"mro_cost_engine_bay": {"fixed": 110000.0, "per_ac": 10000.0},
			"mro_cost_rotables_store": {"fixed": 65000.0, "per_ac": 6666.0},
			"mro_cost_parts_store": {"fixed": 80000.0, "per_ac": 8333.0},
			"mro_cost_utilities": {"fixed": 70000.0, "per_ac": 8333.0},
			"mro_cost_it_quality": {"fixed": 70000.0, "per_ac": 5833.0},
		},
	}


def _calculate_mro_levels_comparison(values):
	"""Calculate costs for all 3 MRO levels across all maintenance approaches."""
	maintenance_approaches = [
		"Parts Supply Only",
		"Scheduled Event Library (detailed PMI)",
		"Scheduled & Unscheduled Event Library (all events)"
	]
	mro_levels = _get_mro_level_presets()
	fleet_size = int(values.get("fleet_size", 1))
	results = []
	
	for approach in maintenance_approaches:
		for level_name, level_costs in mro_levels.items():
			# Create a copy of values for this scenario
			scenario_values = dict(values)
			scenario_values["maintenance_mode"] = approach
			
			# Apply MRO level costs
			for cost_key, components in level_costs.items():
				fixed = float(components.get("fixed", 0.0))
				per_ac = float(components.get("per_ac", 0.0))
				scenario_values[cost_key] = fixed + (per_ac * fleet_size)
			
			# Calculate costs
			try:
				costings_df = _build_costings_dataframe(scenario_values, apply_escalation=True)
				total_cost = float(costings_df["Total Cost"].sum())
				total_fh = float(costings_df["Total FH"].sum())
				cost_per_fh = (total_cost / total_fh) if total_fh > 0 else 0.0
				annual_avg_cost = total_cost / int(values.get("years", 1)) if int(values.get("years", 1)) > 0 else 0.0
			except Exception:
				total_cost = 0.0
				total_fh = 0.0
				cost_per_fh = 0.0
				annual_avg_cost = 0.0
			
			results.append({
				"Maintenance Approach": approach,
				"MRO Level": level_name,
				"Total Cost": total_cost,
				"Total FH": total_fh,
				"Cost/FH": cost_per_fh,
				"Annual Avg Cost": annual_avg_cost,
			})
	
	return pd.DataFrame(results)


def _build_mro_levels_comparison_charts(values):
	"""Build grouped bar charts for MRO capability level comparison."""
	comparison_df = _calculate_mro_levels_comparison(values)
	if comparison_df.empty:
		return comparison_df, None, None

	display_factor = _display_conversion_factor(values)
	currency_symbol = values.get("currency_symbol", "$")
	mro_order = ["Lean", "Standard", "Full"]
	comparison_df = comparison_df.copy()
	comparison_df["MRO Level"] = pd.Categorical(
		comparison_df["MRO Level"],
		categories=mro_order,
		ordered=True,
	)
	comparison_df = comparison_df.sort_values(["Maintenance Approach", "MRO Level"])

	fig_total = go.Figure()
	for approach in comparison_df["Maintenance Approach"].dropna().unique().tolist():
		approach_data = comparison_df[comparison_df["Maintenance Approach"] == approach]
		fig_total.add_trace(go.Bar(
			x=approach_data["MRO Level"],
			y=approach_data["Total Cost"] * display_factor,
			name=approach,
			text=[f"{currency_symbol}{v * display_factor:,.0f}" for v in approach_data["Total Cost"]],
			textposition="outside",
			hovertemplate="<b>%{name}</b><br>MRO Level: %{x}<br>Total Cost: " + currency_symbol + "%{y:,.0f}<extra></extra>",
		))
	fig_total.update_layout(
		title="Total Contract Cost by MRO Level and Maintenance Approach",
		xaxis_title="MRO Capability Level",
		yaxis_title=f"Total Contract Cost ({currency_symbol})",
		barmode="group",
		height=500,
		hovermode="x unified",
		template="plotly_white",
	)

	fig_fh = go.Figure()
	for approach in comparison_df["Maintenance Approach"].dropna().unique().tolist():
		approach_data = comparison_df[comparison_df["Maintenance Approach"] == approach]
		fig_fh.add_trace(go.Bar(
			x=approach_data["MRO Level"],
			y=approach_data["Cost/FH"] * display_factor,
			name=approach,
			text=[f"{currency_symbol}{v * display_factor:,.2f}" for v in approach_data["Cost/FH"]],
			textposition="outside",
			hovertemplate="<b>%{name}</b><br>MRO Level: %{x}<br>Cost/FH: " + currency_symbol + "%{y:,.2f}<extra></extra>",
		))
	fig_fh.update_layout(
		title="Cost per Flight Hour by MRO Level and Maintenance Approach",
		xaxis_title="MRO Capability Level",
		yaxis_title=f"Cost/FH ({currency_symbol})",
		barmode="group",
		height=500,
		hovermode="x unified",
		template="plotly_white",
	)

	return comparison_df, fig_total, fig_fh


def _build_report_sections(values):
	scheduled_path = os.path.join("data", "scheduled_events.csv")
	unsched_path = os.path.join("data", "unscheduled_events.csv")
	scheduled_df = pd.read_csv(scheduled_path) if os.path.exists(scheduled_path) else pd.DataFrame()
	unsched_df = pd.read_csv(unsched_path) if os.path.exists(unsched_path) else pd.DataFrame()

	currency_symbol = values["currency_symbol"]
	display_factor = _display_conversion_factor(values)
	planning_start_date = pd.to_datetime(values["planning_start_date"])
	fleet_size = int(values["fleet_size"])
	annual_hours = float(values["annual_hours_per_ac"])
	contract_years = int(values["years"])
	contingency_pct = float(values.get("geographic_contingency_pct", 0.0))
	contingency_multiplier = 1.0 + (contingency_pct / 100.0)
	contract_fleet_hours_nominal = fleet_size * annual_hours * contract_years
	try:
		costings_df_report = _build_costings_dataframe(values, apply_escalation=True)
		contract_fleet_hours_phased = float(costings_df_report["Total FH"].sum())
		contract_total_cost = float(costings_df_report["Total Cost"].sum())
		contract_avg_annual_cost = (contract_total_cost / contract_years) if contract_years > 0 else 0.0
		contract_overheads = float(costings_df_report["MRO Overheads"].sum()) if "MRO Overheads" in costings_df_report.columns else 0.0
		contract_management_fee = float(costings_df_report["Management Fee"].sum()) if "Management Fee" in costings_df_report.columns else 0.0
		annual_management_fee = (contract_management_fee / contract_years) if contract_years > 0 else 0.0
	except Exception:
		contract_fleet_hours_phased = float(contract_fleet_hours_nominal)
		contract_total_cost = 0.0
		contract_avg_annual_cost = 0.0
		contract_overheads = 0.0
		_base_annual_mgmt_fee = float(values.get("annual_management_fee_per_ac", 0.0)) * fleet_size
		_esc_rate = float(values.get("annual_escalation", 0.0)) / 100.0
		contract_management_fee = sum(
			_base_annual_mgmt_fee * (1 + _esc_rate) ** y for y in range(contract_years)
		) if contract_years > 0 else 0.0
		annual_management_fee = (contract_management_fee / contract_years) if contract_years > 0 else _base_annual_mgmt_fee
	annual_overheads_base, annual_overheads_total = _annual_mro_overheads_from_values(values)
	annual_overheads_total_loaded = annual_overheads_total * contingency_multiplier
	management_fee_per_fh = (contract_management_fee / contract_fleet_hours_phased) if contract_fleet_hours_phased > 0 else 0.0

	mro = _compute_mro_staffing(values)
	contract_cost_per_fh = (contract_total_cost / contract_fleet_hours_phased) if contract_fleet_hours_phased > 0 else 0.0
	manpower_hours_per_fh = (mro['annual_avg_mh'] / (fleet_size * annual_hours)) if fleet_size * annual_hours > 0 else 0.0

	# Dashboard-like downtime and availability summary.
	total_sched_downtime_days = 0.0
	if not scheduled_df.empty:
		event_lookup = {}
		event_types = [e for e in scheduled_df["Scheduled Event"].dropna().unique().tolist() if e not in ("Daily Pre-flight", "")]
		for _, row in scheduled_df.iterrows():
			event = row.get("Scheduled Event")
			if isinstance(event, str):
				event_lookup[event] = {"Downtime": row.get("Downtime")}
		summary = {e: 0 for e in event_types}
		total_fh = 480
		pmi_cycle = ["PMI 1", "PMI 2"]
		pmi_idx = 0
		while total_fh < annual_hours:
			event = pmi_cycle[pmi_idx % 2]
			if event in summary:
				summary[event] += 1
			total_fh += 480
			pmi_idx += 1
		for _, row in scheduled_df.iterrows():
			event = row.get("Scheduled Event")
			if event in ("PMI 1", "PMI 2", "Daily Pre-flight", ""):
				continue
			if event == "90-day Corrosion Check":
				n_events = 4
			elif event == "6-Month Insp":
				n_events = 2
			elif event == "Annual Insp":
				n_events = 1
			else:
				interval_val = row.get("Interval (hrs)")
				interval = float(interval_val) if pd.notna(interval_val) and interval_val != "" else None
				n_events = int(annual_hours / interval) if interval and interval > 0 else 0
			if pd.notna(event) and event in summary:
				summary[event] += n_events
		for event, n_events in summary.items():
			total_sched_downtime_days += _parse_downtime_days(event_lookup.get(event, {}).get("Downtime", "")) * n_events

	total_unsched_downtime_days = 0.0
	if _is_unscheduled_library_mode(values.get("maintenance_mode")) and not unsched_df.empty:
		for _, row in unsched_df.iterrows():
			total_unsched_downtime_days += _parse_downtime_days(row.get("Downtime", 0.0)) * _expected_unscheduled_events(annual_hours)

	total_days = 365 * fleet_size
	tech_avail_pct = max(0.0, min((1 - ((total_sched_downtime_days + total_unsched_downtime_days) / total_days)) * 100, 100)) if total_days > 0 else 0.0

	maintenance_summary = {}
	for ac_idx in range(fleet_size):
		ac_summary = _count_events_for_ac_report(ac_idx, values, scheduled_df)
		for event_name, count in ac_summary.items():
			maintenance_summary[event_name] = maintenance_summary.get(event_name, 0) + count
	if _is_unscheduled_library_mode(values.get("maintenance_mode")) and not unsched_df.empty:
		for _, row in unsched_df.iterrows():
			event = row.get("Unscheduled Event")
			if isinstance(event, str):
				maintenance_summary[event] = _expected_unscheduled_events(annual_hours) * contract_years * fleet_size

	sections = []
	fh_delta = contract_fleet_hours_nominal - contract_fleet_hours_phased
	fh_lines = [f"Contract Fleet Hours (Phased): {contract_fleet_hours_phased:,.1f}"]
	if abs(fh_delta) > 0.05:
		fh_lines.append(f"Contract Fleet Hours (Nominal): {contract_fleet_hours_nominal:,.1f}")
		fh_lines.append("Note: Difference reflects phased aircraft introduction over the contract period.")
	sections.append(("Dashboard", [
		f"Scenario: {values.get('scenario_name', 'N/A')}",
		f"Fleet Size: {fleet_size}",
		f"Annual Fleet (FH): {int(fleet_size * annual_hours)}",
		f"Contract Length: {contract_years} years",
		f"Display Currency: {values.get('currency')}",
		f"FX Source: {values.get('fx_source', 'Static model assumption')}",
		f"FX Timestamp: {values.get('fx_timestamp', 'N/A')}",
		f"FX Rate: 1 {values.get('currency')} = {float(values.get('rate_to_usd', 1.0)):.4f} USD",
		f"FX Rate: 1 USD = {float(values.get('conversion_factor', 1.0)):.4f} {values.get('currency')}",
		f"Geographic Contingency: {contingency_pct:.1f}%",
	] + fh_lines + [
		f"Contract Cost / FH: {_format_currency(currency_symbol, contract_cost_per_fh * display_factor, 2)}",
		f"Average Annual Cost: {_format_currency(currency_symbol, contract_avg_annual_cost * display_factor, 0)}",
		f"Manpower Hours/FH: {manpower_hours_per_fh:.2f}",
		f"Scheduled Downtime per A/C per Year: {total_sched_downtime_days:.1f} days",
		f"Unscheduled Downtime per A/C per Year: {total_unsched_downtime_days:.1f} days",
		f"Technical Availability: {tech_avail_pct:.1f}%",
	]))
	sections.append(("Maintenance Schedule", [
		f"Planning Start Date: {planning_start_date.strftime('%d/%m/%Y')}",
		f"Maintenance Mode: {values.get('maintenance_mode')}",
		"Planning Horizon Event Totals (Fleet):",
	] + [f"- {event_name}: {int(round(float(count)))}" for event_name, count in sorted(maintenance_summary.items()) if count > 0]))
	sections.append(("Availability & Downtime", [
		f"Target Availability: {float(values.get('target_availability', 75.0)):.1f}%",
		f"Scheduled Downtime per A/C per Year: {total_sched_downtime_days:.1f} days",
		f"Unscheduled Downtime per A/C per Year: {total_unsched_downtime_days:.1f} days",
		f"Technical Availability: {tech_avail_pct:.1f}%",
	] + [f"- Aircraft {idx + 1} start: {_get_aircraft_start(values, idx, planning_start_date).strftime('%d/%m/%Y')}" for idx in range(fleet_size)]))
	sections.append(("Costings", [
		f"Contract Period: {planning_start_date.strftime('%d/%m/%Y')} - {(planning_start_date + pd.DateOffset(years=contract_years) - pd.Timedelta(days=1)).strftime('%d/%m/%Y')}",
		f"Contract Average Annual Cost: {_format_currency(currency_symbol, contract_avg_annual_cost * display_factor, 0)}",
		f"Total Contract Cost: {_format_currency(currency_symbol, contract_total_cost * display_factor, 0)}",
		f"Annual MRO Overheads (loaded): {_format_currency(currency_symbol, annual_overheads_total_loaded * display_factor, 0)}",
		f"Contract MRO Overheads: {_format_currency(currency_symbol, contract_overheads * display_factor, 0)}",
		f"Contract FH (Phased): {contract_fleet_hours_phased:,.1f}",
		f"Contract FH (Nominal): {contract_fleet_hours_nominal:,.1f}",
		f"Display Currency: {values.get('currency')}",
		f"FX Source: {values.get('fx_source', 'Static model assumption')}",
		f"FX Timestamp: {values.get('fx_timestamp', 'N/A')}",
		f"Contract Management Fee: {_format_currency(currency_symbol, contract_management_fee * display_factor, 0)}",
		f"Annual Management Fee: {_format_currency(currency_symbol, annual_management_fee * display_factor, 0)}",
		f"Management Fee / FH: {_format_currency(currency_symbol, management_fee_per_fh * display_factor, 2)}",
		f"Geographic Contingency: {contingency_pct:.1f}%",
		f"Labour Rate: {_format_currency(currency_symbol, float(values.get('labour_rate', 0.0)) * display_factor, 2)} per hour",
		f"Escalation Rate: {float(values.get('annual_escalation', 0.0)):.1f}%",
	] + [
		f"{label}: {_format_currency(currency_symbol, amount * contingency_multiplier * display_factor, 0)} per year"
		for label, amount in annual_overheads_base.items()
	]))
	try:
		mro_comp_df = _calculate_mro_levels_comparison(values)
		if not mro_comp_df.empty:
			min_row = mro_comp_df.loc[mro_comp_df["Total Cost"].idxmin()]
			max_row = mro_comp_df.loc[mro_comp_df["Total Cost"].idxmax()]
			comparison_lines = [
				"Comparison includes Lean, Standard, and Full capability overhead profiles across all maintenance approaches.",
				f"Lowest Total Cost: {min_row['Maintenance Approach']} / {min_row['MRO Level']} = {_format_currency(currency_symbol, float(min_row['Total Cost']) * display_factor, 0)}",
				f"Highest Total Cost: {max_row['Maintenance Approach']} / {max_row['MRO Level']} = {_format_currency(currency_symbol, float(max_row['Total Cost']) * display_factor, 0)}",
			]
		else:
			comparison_lines = ["No comparison data available for current inputs."]
	except Exception:
		comparison_lines = ["Comparison data unavailable due to calculation error."]
	sections.append(("MRO Capability Levels Comparison", comparison_lines))
	sections.append(("Event Library", [
		f"Scheduled Events Rows: {len(scheduled_df)}",
		f"Unscheduled Events Rows: {len(unsched_df)}",
		"Scheduled Events Preview:",
	] + (scheduled_df.head(8).to_string(index=False).splitlines() if not scheduled_df.empty else ["No scheduled events loaded."]) + [
		"Unscheduled Events Preview:",
	] + (unsched_df.head(8).to_string(index=False).splitlines() if not unsched_df.empty else ["No unscheduled events loaded."])))

	cs = currency_symbol
	mro_lines = [
		f"Productive hrs/person/year: {mro['mp_productive_hrs']}",
		f"Mechanics per LAE: {mro['mp_engineer_ratio']}",
		f"Direct maintainers per QC Inspector: {mro['mp_qc_ratio']}",
		f"Aircraft per Parts Coordinator: {mro['mp_parts_ratio']}",
		f"Shift coverage: {mro['mp_shift_coverage']}",
		f"Planning Officer included: {'Yes' if mro['mp_include_planning'] else 'No'}",
		"",
		f"Licensed Aircraft Engineers (LAE): {mro['lae_count']}",
		f"Aircraft Mechanics: {mro['mechanic_count']}",
		f"QC / QA Inspectors: {mro['qc_count']}",
		f"Parts / Logistics Coordinators: {mro['parts_count']}",
		f"Planning & Engineering Officers: {mro['planning_count']}",
		f"Total Headcount: {mro['total_staff']}",
		f"Direct Staff Utilisation: {mro['utilisation_pct']:.1f}%",
		f"Annual Staff Cost: {_format_currency(cs, mro['annual_staff_cost'] * display_factor, 0)}",
		f"Annual MRO Labour Charge: {_format_currency(cs, mro['annual_charge'] * display_factor, 0)}",
	]
	for yr_data in mro["per_year"]:
		mro_lines.append(
			f"Year {yr_data['year']} ({yr_data['period']}): "
			f"Total={yr_data['total']}  LAE={yr_data['lae']}  "
			f"Mechs={yr_data['mechanics']}  QC={yr_data['qc']}  "
			f"Parts={yr_data['parts']}  Util={yr_data['util_pct']:.1f}%  "
			f"Charge={_format_currency(cs, yr_data['charge'] * display_factor, 0)}"
		)
	sections.append(("MRO Manpower Planning", mro_lines))

	return sections


def _build_pdf_report(values):
	from reportlab.lib.pagesizes import A4
	from reportlab.lib.utils import ImageReader, simpleSplit
	from reportlab.pdfgen import canvas

	buffer = BytesIO()
	pdf = canvas.Canvas(buffer, pagesize=A4)
	page_width, page_height = A4
	margin = 40
	logo_path = os.path.join("ui", "acehawk_logo.png")
	timestamp = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M:%S")
	scenario_name = str(values.get("scenario_name", "") or "")

	def draw_page_header():
		if os.path.exists(logo_path):
			pdf.drawImage(ImageReader(logo_path), margin, page_height - 70, width=90, height=36, mask='auto', preserveAspectRatio=True)
		pdf.setFont("Helvetica-Bold", 10)
		pdf.drawString(margin + 100, page_height - 52, "COMMERCIAL-IN-CONFIDENCE")
		pdf.setFont("Helvetica-Bold", 16)
		pdf.drawString(margin, page_height - 90, "AceHawk UH-60 Maintenance Report")
		if scenario_name:
			pdf.setFont("Helvetica-Oblique", 10)
			pdf.drawString(margin, page_height - 106, f"Scenario: {scenario_name}")
		pdf.setFont("Helvetica", 9)
		pdf.drawRightString(page_width - margin, page_height - 30, f"Generated: {timestamp}")
		pdf.setFont("Helvetica-Oblique", 9)
		pdf.drawCentredString(page_width / 2, margin - 8, "For Illustrative Purposes only, not Contract Binding")

	def ensure_space(current_y, needed_height):
		if current_y - needed_height < margin:
			pdf.showPage()
			draw_page_header()
			return page_height - 110
		return current_y

	sections = _build_report_sections(values)
	maintenance_schedule_fig = _build_maintenance_schedule_figure(values)
	maintenance_timeline_fig = _build_maintenance_timeline_figure(values)
	costings_fh_cost_fig = _build_costings_fh_cost_figure(values)
	overheads_fh_cost_fig = _build_overheads_fh_cost_figure(values)
	mro_comparison_df = pd.DataFrame()
	mro_comparison_total_fig = None
	mro_comparison_fh_fig = None
	mro_comparison_error = None
	try:
		mro_comparison_df, mro_comparison_total_fig, mro_comparison_fh_fig = _build_mro_levels_comparison_charts(values)
	except Exception as exc:
		mro_comparison_error = str(exc)
		mro_comparison_df = pd.DataFrame()
	draw_page_header()
	y = page_height - 120

	def draw_chart_in_pdf(fig, current_y):
		if fig is None:
			return current_y
		try:
			img_bytes = fig.to_image(format="png", width=1400, height=800, scale=2)
		except Exception:
			current_y = ensure_space(current_y, 14)
			pdf.setFont("Helvetica-Oblique", 9)
			pdf.drawString(margin, current_y, "Chart export unavailable in this environment (install kaleido for Plotly image rendering).")
			return current_y - 14

		chart_width = page_width - (2 * margin)
		chart_height = chart_width * 0.5
		current_y = ensure_space(current_y, chart_height + 12)
		img_reader = ImageReader(BytesIO(img_bytes))
		pdf.drawImage(img_reader, margin, current_y - chart_height, width=chart_width, height=chart_height, preserveAspectRatio=True, mask='auto')
		return current_y - chart_height - 10

	def draw_mro_table_in_pdf(mro_data, current_y):
		from reportlab.platypus import Table, TableStyle
		from reportlab.lib import colors as rl_colors
		cs_sym = values.get("currency_symbol", "$")
		display_factor = _display_conversion_factor(values)
		lc = float(values.get("labour_cost", 0.0)) * display_factor
		lr = float(values.get("labour_rate", 0.0)) * display_factor
		cm = 1.0 + float(values.get("geographic_contingency_pct", 0.0)) / 100.0
		pph = mro_data["mp_productive_hrs"]

		def _role_row(role, count):
			h = count * pph
			return [role, str(count), f"{h:,}", f"{cs_sym}{count * pph * lc:,.0f}", f"{cs_sym}{count * pph * lr * cm:,.0f}"]

		rows = [
			["Role", "Headcount", "Ann. Hrs", "Staff Cost", "MRO Charge"],
			_role_row("Licensed Aircraft Engineer (LAE)", mro_data["lae_count"]),
			_role_row("Aircraft Mechanic", mro_data["mechanic_count"]),
			_role_row("QC / Quality Assurance Inspector", mro_data["qc_count"]),
			_role_row("Parts / Logistics Coordinator", mro_data["parts_count"]),
		]
		if mro_data["planning_count"]:
			rows.append(_role_row("Planning & Engineering Officer", mro_data["planning_count"]))
		rows.append(["TOTAL", str(mro_data["total_staff"]),
		             f"{mro_data['total_staff'] * pph:,}",
		             f"{cs_sym}{(mro_data['annual_staff_cost'] * display_factor):,.0f}",
		             f"{cs_sym}{(mro_data['annual_charge'] * display_factor):,.0f}"])
		col_widths = [175, 50, 65, 70, 70]
		tbl = Table(rows, colWidths=col_widths)
		tbl.setStyle(TableStyle([
			("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1a4b7c")),
			("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
			("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
			("FONTSIZE", (0, 0), (-1, -1), 8),
			("BACKGROUND", (0, -1), (-1, -1), rl_colors.HexColor("#dce8f5")),
			("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
			("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#aaaaaa")),
			("ROWBACKGROUNDS", (0, 1), (-1, -2), [rl_colors.white, rl_colors.HexColor("#f2f6fb")]),
			("TOPPADDING", (0, 0), (-1, -1), 3),
			("BOTTOMPADDING", (0, 0), (-1, -1), 3),
			("ALIGN", (1, 0), (-1, -1), "RIGHT"),
		]))
		tw, th = tbl.wrapOn(pdf, page_width - 2 * margin, page_height)
		current_y = ensure_space(current_y, th + 20)
		pdf.setFont("Helvetica-Bold", 10)
		pdf.drawString(margin, current_y, "Annual Average Staffing Summary")
		current_y -= 14
		tbl.drawOn(pdf, margin, current_y - th)
		current_y -= th + 16

		if mro_data["per_year"]:
			yr_rows = [["Year", "Period", "Maint. Hrs", "LAE", "Mechs", "QC", "Parts", "Total", "Util %", "MRO Charge"]]
			for yr in mro_data["per_year"]:
				yr_rows.append([
					str(yr["year"]), yr["period"], f"{yr['mh']:,.0f}",
					str(yr["lae"]), str(yr["mechanics"]), str(yr["qc"]), str(yr["parts"]),
					str(yr["total"]), f"{yr['util_pct']:.1f}%", f"{cs_sym}{(yr['charge'] * display_factor):,.0f}",
				])
			yr_col_widths = [28, 100, 55, 25, 28, 25, 28, 28, 35, 68]
			yr_tbl = Table(yr_rows, colWidths=yr_col_widths)
			yr_tbl.setStyle(TableStyle([
				("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1a4b7c")),
				("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
				("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
				("FONTSIZE", (0, 0), (-1, -1), 8),
				("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#aaaaaa")),
				("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, rl_colors.HexColor("#f2f6fb")]),
				("TOPPADDING", (0, 0), (-1, -1), 3),
				("BOTTOMPADDING", (0, 0), (-1, -1), 3),
				("ALIGN", (2, 0), (-1, -1), "RIGHT"),
			]))
			yt_w, yt_h = yr_tbl.wrapOn(pdf, page_width - 2 * margin, page_height)
			current_y = ensure_space(current_y, yt_h + 20)
			pdf.setFont("Helvetica-Bold", 10)
			pdf.drawString(margin, current_y, "Staffing Requirement by Contract Year")
			current_y -= 14
			yr_tbl.drawOn(pdf, margin, current_y - yt_h)
			current_y -= yt_h + 10
		return current_y

	def draw_mro_comparison_table_in_pdf(comparison_df, current_y):
		from reportlab.platypus import Table, TableStyle
		from reportlab.lib import colors as rl_colors
		if comparison_df is None or comparison_df.empty:
			return current_y

		cs_sym = values.get("currency_symbol", "$")
		display_factor = _display_conversion_factor(values)
		rows = [["Maintenance Approach", "MRO Level", "Total Cost", "Cost/FH", "Avg Annual Cost"]]
		for _, row in comparison_df.iterrows():
			rows.append([
				str(row.get("Maintenance Approach", "")),
				str(row.get("MRO Level", "")),
				f"{cs_sym}{float(row.get('Total Cost', 0.0)) * display_factor:,.0f}",
				f"{cs_sym}{float(row.get('Cost/FH', 0.0)) * display_factor:,.2f}",
				f"{cs_sym}{float(row.get('Annual Avg Cost', 0.0)) * display_factor:,.0f}",
			])

		col_widths = [150, 60, 95, 75, 95]
		tbl = Table(rows, colWidths=col_widths)
		tbl.setStyle(TableStyle([
			("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1a4b7c")),
			("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
			("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
			("FONTSIZE", (0, 0), (-1, -1), 7),
			("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#aaaaaa")),
			("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, rl_colors.HexColor("#f2f6fb")]),
			("TOPPADDING", (0, 0), (-1, -1), 3),
			("BOTTOMPADDING", (0, 0), (-1, -1), 3),
			("ALIGN", (2, 0), (-1, -1), "RIGHT"),
		]))
		tw, th = tbl.wrapOn(pdf, page_width - 2 * margin, page_height)
		current_y = ensure_space(current_y, th + 20)
		pdf.setFont("Helvetica-Bold", 10)
		pdf.drawString(margin, current_y, "MRO Capability Level Cost Comparison")
		current_y -= 14
		tbl.drawOn(pdf, margin, current_y - th)
		return current_y - th - 10

	mro_staffing_for_pdf = _compute_mro_staffing(values)
	for section_title, section_lines in sections:
		if section_title in ("Costings", "MRO Manpower Planning"):
			pdf.showPage()
			draw_page_header()
			y = page_height - 120
		y = ensure_space(y, 24)
		pdf.setFont("Helvetica-Bold", 13)
		pdf.drawString(margin, y, section_title)
		y -= 18
		pdf.setFont("Helvetica", 9)
		for line in section_lines:
			wrapped_lines = simpleSplit(str(line), "Helvetica", 9, page_width - (2 * margin))
			for wrapped_line in wrapped_lines:
				y = ensure_space(y, 12)
				pdf.drawString(margin, y, wrapped_line)
				y -= 11
		if section_title == "Maintenance Schedule":
			y -= 4
			y = draw_chart_in_pdf(maintenance_schedule_fig, y)
			y = draw_chart_in_pdf(maintenance_timeline_fig, y)
		if section_title == "Costings":
			y -= 4
			y = draw_chart_in_pdf(costings_fh_cost_fig, y)
			y = draw_chart_in_pdf(overheads_fh_cost_fig, y)
		if section_title == "MRO Manpower Planning":
			y = draw_mro_table_in_pdf(mro_staffing_for_pdf, y)
		y -= 8

	# Always render a dedicated MRO comparison page in the PDF so print/download includes the table and graphs.
	pdf.showPage()
	draw_page_header()
	y = page_height - 120
	pdf.setFont("Helvetica-Bold", 13)
	pdf.drawString(margin, y, "MRO Capability Levels Comparison")
	y -= 18
	pdf.setFont("Helvetica", 9)
	if mro_comparison_error:
		y = ensure_space(y, 12)
		pdf.drawString(margin, y, f"Unable to calculate MRO comparison charts: {mro_comparison_error}")
		y -= 12
	if mro_comparison_df.empty:
		y = ensure_space(y, 12)
		pdf.drawString(margin, y, "No MRO capability comparison data available for current inputs.")
		y -= 12
	else:
		y = draw_mro_comparison_table_in_pdf(mro_comparison_df, y)
		if mro_comparison_total_fig is not None:
			y = draw_chart_in_pdf(mro_comparison_total_fig, y)
		else:
			y = ensure_space(y, 12)
			pdf.drawString(margin, y, "Total Cost comparison graph unavailable.")
			y -= 12
		if mro_comparison_fh_fig is not None:
			y = draw_chart_in_pdf(mro_comparison_fh_fig, y)
		else:
			y = ensure_space(y, 12)
			pdf.drawString(margin, y, "Cost/FH comparison graph unavailable.")
			y -= 12

	pdf.save()
	buffer.seek(0)
	filename_timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
	filename = f"AceHawk_UH60_Report_{filename_timestamp}.pdf"
	email_subject = f"AceHawk UH-60 Report {filename_timestamp}"
	email_body = f"Please find attached the UH-60 maintenance report generated on {timestamp}. Download the PDF from the app and attach it to this email."
	return buffer.getvalue(), filename, email_subject, email_body


def _render_print_report_actions(values):
	st.subheader("Print Report")
	# Augment values with scenario name and MRO staffing params from current session state
	report_values = dict(values)
	report_values["scenario_name"] = st.session_state.get(
		"scenario_last_loaded", st.session_state.get("scenario_name_input", "N/A")
	)
	report_values["mp_productive_hrs"] = int(st.session_state.get("mp_productive_hrs", 1500))
	report_values["mp_engineer_ratio"] = int(st.session_state.get("mp_engineer_ratio", 3))
	report_values["mp_qc_ratio"] = int(st.session_state.get("mp_qc_ratio", 8))
	report_values["mp_parts_ratio"] = int(st.session_state.get("mp_parts_ratio", 4))
	report_values["mp_include_planning"] = bool(st.session_state.get("mp_include_planning", True))
	report_values["mp_shift_coverage"] = str(st.session_state.get("mp_shift_coverage", "Single shift (1×)"))
	for _, overhead_key in _mro_overhead_categories():
		report_values[overhead_key] = float(st.session_state.get(overhead_key, 0.0))
	# Force a fresh costing pass for the PDF so report values never come from stale cache entries.
	try:
		_build_costings_dataframe.clear()
	except Exception:
		pass
	try:
		report_pdf, report_filename, email_subject, email_body = _build_pdf_report(report_values)
	except ModuleNotFoundError:
		st.warning("PDF reporting is unavailable because the ReportLab package is not installed in the Python environment used by Streamlit.")
		st.info("Install it in the interpreter running the app with: .venv\\Scripts\\python.exe -m pip install reportlab")
		return
	pdf_b64 = base64.b64encode(report_pdf).decode("utf-8")

	col1, col2 = st.columns(2)
	with col1:
		st.download_button(
			"Download PDF Report",
			data=report_pdf,
			file_name=report_filename,
			mime="application/pdf",
			use_container_width=True,
		)
	with col2:
		st.link_button(
			"Email Report Draft",
			f"mailto:?subject={quote(email_subject)}&body={quote(email_body)}",
			use_container_width=True,
		)
	components.html(
		f"""
		<div style=\"margin-top: 0.5rem;\">
		  <button onclick=\"printPdfReport()\" style=\"background:#0f766e;color:white;border:none;border-radius:0.5rem;padding:0.6rem 1rem;cursor:pointer;\">Print PDF Report</button>
		</div>
		<script>
		  const pdfBase64 = "{pdf_b64}";

		  function base64ToBlob(base64, mimeType) {{
		    const binary = atob(base64);
		    const bytes = new Uint8Array(binary.length);
		    for (let i = 0; i < binary.length; i += 1) {{
		      bytes[i] = binary.charCodeAt(i);
		    }}
		    return new Blob([bytes], {{ type: mimeType }});
		  }}

		  function printPdfReport() {{
		    try {{
		      const pdfBlob = base64ToBlob(pdfBase64, "application/pdf");
		      const pdfUrl = URL.createObjectURL(pdfBlob);
		      const printWindow = window.open(pdfUrl, "_blank");
		      if (!printWindow) {{
		        alert("Pop-up blocked. Please allow pop-ups for this app or use Download PDF Report.");
		        URL.revokeObjectURL(pdfUrl);
		        return;
		      }}
		      try {{
		        printWindow.focus();
		      }} catch (error) {{
		        console.error("Failed to focus PDF window", error);
		      }}

		      setTimeout(function() {{
		        try {{
		          URL.revokeObjectURL(pdfUrl);
		        }} catch (error) {{
		          console.error("Failed to revoke PDF object URL", error);
		        }}
		      }}, 60000);
		    }} catch (error) {{
		      console.error("PDF print failed", error);
		      alert("Unable to print the PDF directly. Please use Download PDF Report and print the file locally.");
		    }}
		  }}
		</script>
		""",
		height=60,
	)
	st.caption("The PDF includes the AceHawk logo, timestamp, and sections covering all tabs. Print PDF Report opens the full multi-page document in your browser's PDF viewer for printing.")

# Dashboard metrics function
def show_dashboard(values):
	# Display AceHawk logo at top left
	logo_path = os.path.join("ui", "acehawk_logo.png")
	if os.path.exists(logo_path):
		st.image(logo_path, width=100)
	
	st.header("Dashboard")
	col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
	with col1:
		st.metric("Fleet Size", values["fleet_size"])
	with col2:
		st.metric("Annual Fleet (FH)", values["fleet_size"] * values["annual_hours_per_ac"])
	with col3:
		st.metric("Contract Length", f"{values['years']} yrs")
	# Calculate Contract Fleet Hours
	contract_fleet_hours = 0
	for i in range(values["fleet_size"]):
		# Use exact years for contract fleet hours calculation
		contract_fleet_hours += values["years"] * values["annual_hours_per_ac"]

	# Compute cost metrics directly from annual costings engine so dashboard matches costings tab.
	display_factor = _display_conversion_factor(values)
	try:
		costings_df = _build_costings_dataframe(values, apply_escalation=True)
		_contract_fh_total = float(costings_df["Total FH"].sum())
		_contract_total_cost = float(costings_df["Total Cost"].sum())
		_contract_total_mh = float(costings_df["Manpower Hrs"].sum())
		_n_years = int(values["years"])
		_cost_per_fh = _contract_total_cost / _contract_fh_total if _contract_fh_total > 0 else 0.0
		_mh_per_fh = _contract_total_mh / _contract_fh_total if _contract_fh_total > 0 else 0.0
		_avg_annual_cost = _contract_total_cost / _n_years if _n_years > 0 else 0.0
		contract_fleet_hours = _contract_fh_total
	except Exception:
		_contract_total_cost = 0.0
		_cost_per_fh = 0.0
		_mh_per_fh = 0.0
		_avg_annual_cost = 0.0

	esc_pct = float(values.get("annual_escalation", 0.0))
	contingency_pct = float(values.get("geographic_contingency_pct", 0.0))
	if esc_pct > 0:
		esc_note = f"With annual escalation of {esc_pct:.1f}% and geographic contingency of {contingency_pct:.1f}%"
	else:
		esc_note = f"Without annual escalation (geographic contingency {contingency_pct:.1f}%)"

	with col4:
		st.metric("Contract Fleet Hours", f"{int(contract_fleet_hours)}")
	with col5:
		st.metric("Contract Cost / FH", f"{values['currency_symbol']}{(_cost_per_fh * display_factor):,.0f}")
	with col6:
		st.metric("Average Annual Cost", f"{values['currency_symbol']}{(_avg_annual_cost * display_factor):,.0f}")
	with col7:
		st.metric("Total Contract Cost", f"{values['currency_symbol']}{(_contract_total_cost * display_factor):,.0f}")
		st.caption(esc_note)

	# Scheduled Downtime calculation (match Maintenance Schedule Fleet Forecast Downtime)
	# Scheduled Downtime: annual sum per aircraft
	try:
		scheduled_df = pd.read_csv(os.path.join("data", "scheduled_events.csv"))
		annual_hours = values["annual_hours_per_ac"]
		event_types = scheduled_df["Scheduled Event"].dropna().unique().tolist()
		event_types = [e for e in event_types if e not in ("Daily Pre-flight", "")]
		event_lookup = {}
		for _, row in scheduled_df.iterrows():
			event = row["Scheduled Event"]
			if event:
				event_lookup[event] = {
					"Downtime": row["Downtime"]
				}
		summary = {e: 0 for e in event_types}
		# PMI events (annualized)
		fh = 480  # Use default for annualized PMI
		total_fh = fh
		pmi_cycle = ["PMI 1", "PMI 2"]
		pmi_idx = 0
		while total_fh < annual_hours:
			event = pmi_cycle[pmi_idx % 2]
			if event in summary:
				summary[event] += 1
			total_fh += 480
			pmi_idx += 1
		# FH-based and calendar-based events (annualized)
		for _, row in scheduled_df.iterrows():
			event = row["Scheduled Event"]
			if event in ("PMI 1", "PMI 2", "Daily Pre-flight", ""):
				continue
			if event == "90-day Corrosion Check":
				n_events = 4
			elif event == "6-Month Insp":
				n_events = 2
			elif event == "Annual Insp":
				n_events = 1
			else:
				interval_val = row["Interval (hrs)"]
				if pd.notna(interval_val) and interval_val != '':
					interval = float(interval_val)
					n_events = int(annual_hours / interval) if interval > 0 else 0
				else:
					n_events = 0
			if pd.isna(event) or event not in summary:
				continue
			summary[event] += n_events
		def forecast_downtime_days(downtime_str, n_events):
			if not downtime_str or "day" not in str(downtime_str):
				return 0.0
			try:
				days = float(str(downtime_str).split()[0])
			except Exception:
				days = 0.0
			return days * n_events
		total_sched_downtime_days = 0
		for e, n in summary.items():
			vals = event_lookup.get(e, {"Downtime": ""})
			total_sched_downtime_days += forecast_downtime_days(vals["Downtime"], n)
	except Exception:
		total_sched_downtime_days = 0

	# Unscheduled Downtime: annual sum per aircraft
	unscheduled_mode_enabled = _is_unscheduled_library_mode(values.get("maintenance_mode")) or bool(values.get("use_unsched_event_library", False))
	if unscheduled_mode_enabled:
		try:
			unsched_df = pd.read_csv(os.path.join("data", "unscheduled_events.csv"))
			annual_hours = float(values.get("annual_hours_per_ac", 0.0))
			total_unsched_downtime_days = 0
			expected_unsched_events = _expected_unscheduled_events(annual_hours)
			for _, row in unsched_df.iterrows():
				downtime = _parse_downtime_days(row.get("Downtime", 0.0))
				total_unsched_downtime_days += downtime * expected_unsched_events
		except Exception:
			total_unsched_downtime_days = 0
	else:
		total_unsched_downtime_days = 0.0

	# Technical Availability = (Total time - downtime) / Total time
	fleet_size = values["fleet_size"]
	total_days = 365 * fleet_size
	tech_avail = 1.0
	if total_days > 0:
		tech_avail = 1 - (total_sched_downtime_days + total_unsched_downtime_days) / total_days
	tech_avail_pct = max(0, min(tech_avail * 100, 100))

	col5, col6, col7, col8 = st.columns(4)
	with col5:
		st.metric("Scheduled Downtime per A/C per Year (days)", f"{total_sched_downtime_days:.1f}")
	with col6:
		st.metric("Unscheduled Downtime per A/C per Year (days)", f"{total_unsched_downtime_days:.1f}")
	with col7:
		st.metric("Technical Availability (%)", f"{tech_avail_pct:.1f}")
	with col8:
		st.metric("Manpower Hours/FH", f"{_mh_per_fh:.2f}")

	_render_print_report_actions(values)


def _refresh_startup_caches_once():
	if not st.session_state.get("startup_cache_refreshed", False):
		try:
			_build_costings_dataframe.clear()
		except Exception:
			pass
		st.session_state["startup_cache_refreshed"] = True

# Show dashboard ONCE at the top
_refresh_startup_caches_once()
show_dashboard(sidebar_values)

# Navigation tabs

tabs = [
    "Maintenance Schedule",
    "Availability & Downtime",
    "Costings",
    "Event Library"
]

selected_tab = st.tabs(tabs)

for i, tab in enumerate(selected_tab):
	with tab:
		st.subheader(tabs[i])

		if tabs[i] == "Maintenance Schedule":
			st.header("Maintenance Schedule")

			# Load scheduled events
			scheduled_path = os.path.join("data", "scheduled_events.csv")
			if os.path.exists(scheduled_path):
				scheduled_df = pd.read_csv(scheduled_path)
			else:
				scheduled_df = pd.DataFrame()

			fleet_size = sidebar_values["fleet_size"]
			planning_years = sidebar_values["years"]
			annual_hours = sidebar_values["annual_hours_per_ac"]
			planning_hours = planning_years * annual_hours
			hours_until_pmi = sidebar_values["hours_until_pmi"]


			# Prepare event types and normalization mapping
			raw_event_types = scheduled_df["Scheduled Event"].dropna().unique().tolist()
			event_types = [e for e in raw_event_types if e not in ("Daily Pre-flight", "")]  # Exclude Daily Pre-flight and blanks
			event_name_map = {e.strip().lower(): e for e in event_types}
			normalized_event_types = [e.strip().lower() for e in event_types]

			# Aircraft selector
			ac_options = [f"Aircraft {i+1}" for i in range(fleet_size)]
			ac_options.insert(0, "Fleet")
			selected_ac = st.selectbox("Select Aircraft or Fleet", ac_options, key="sched_ac_select")

			def count_events_for_ac(ac_idx):
				# Determine start and end date for this aircraft
				if sidebar_values.get("use_custom_ac_dates") and len(sidebar_values.get("custom_ac_dates", [])) > ac_idx:
					ac_start_date = pd.to_datetime(sidebar_values["custom_ac_dates"][ac_idx])
				else:
					ac_start_date = pd.to_datetime(sidebar_values["planning_start_date"])

				# Contract end date is ac_start_date + years (per aircraft)
				contract_end_date = ac_start_date + pd.DateOffset(years=sidebar_values["years"])
				fh = float(hours_until_pmi[ac_idx])
				summary = {e: 0 for e in normalized_event_types}
				pmi_cycle = ["pmi 1", "pmi 2"]
				pmi_idx = 0
				event_dates = {e: [] for e in normalized_event_types}
				# Count events against exact planned FH to include endpoint occurrences.
				contract_fh = float(sidebar_values["years"]) * float(sidebar_values["annual_hours_per_ac"])
				fh_tolerance = 1e-6
				# PMI events (per-ac), start at initial FH (fh), only count those within contract period
				# Find the first PMI event after initial FH
				first_pmi_offset = fh if fh > 0 else 480.0
				pmi_idx = 0
				next_pmi_fh = first_pmi_offset
				while next_pmi_fh <= contract_fh + fh_tolerance:
					event = pmi_cycle[pmi_idx % 2]
					if event in summary:
						summary[event] += 1
					next_pmi_fh += 480
					pmi_idx += 1
				# FH-based and calendar-based events (per-ac)
				for _, row in scheduled_df.iterrows():
					event = row["Scheduled Event"]
					if not isinstance(event, str):
						continue
					event_key = event.strip().lower()
					if event_key in ("pmi 1", "pmi 2", "daily pre-flight", ""):
						continue
					# Calendar-based events
					if event_key == "90-day corrosion check":
						interval_days = 90
					elif event_key == "6-month insp":
						interval_days = 182
					elif event_key == "annual insp":
						interval_days = 365
					else:
						interval_val = row["Interval (hrs)"]
						if pd.notna(interval_val) and interval_val != '':
							interval = float(interval_val)
						else:
							interval = None

					# Calendar-based
					if event_key in ("90-day corrosion check", "6-month insp", "annual insp"):
						n_events = int((contract_end_date - ac_start_date).days // interval_days)
						for n in range(n_events):
							next_date = ac_start_date + pd.Timedelta(days=n * interval_days)
							years_since_start = (next_date - ac_start_date).days / 365.25
							if next_date > contract_end_date:
								break
							if years_since_start >= 0:
								summary[event_key] += 1
								event_dates.setdefault(event_key, []).append(next_date)
					# FH-based
					elif interval is not None and interval > 0:
						# FH-based events: start at initial FH, only count those within contract period
						fh_pointer = fh
						while True:
							fh_pointer += interval
							if fh_pointer - fh > contract_fh + fh_tolerance:
								break
							summary[event_key] += 1
				return summary


			# Prepare event library lookup
			event_lookup = {}
			for _, row in scheduled_df.iterrows():
				event = row["Scheduled Event"]
				if not isinstance(event, str):
					continue
				event_key = event.strip().lower()
				event_lookup[event_key] = {
					"Man-Hours": float(row["Man-Hours"]) if not pd.isna(row["Man-Hours"]) else 0.0,
					"Parts $ / event": float(row["Parts $ / event"]) if not pd.isna(row["Parts $ / event"]) else 0.0,
					"Downtime": row["Downtime"]
				}

			def forecast_downtime_days(downtime_str, n_events):
				if not downtime_str or "day" not in str(downtime_str):
					return 0.0
				try:
					days = float(str(downtime_str).split()[0])
				except Exception:
					days = 0.0
				return days * n_events


			# Compute summary for Fleet or Aircraft, then build table
			if selected_ac == "Fleet":
				summary = {e: 0 for e in normalized_event_types}
				for ac_idx in range(fleet_size):
					ac_summary = count_events_for_ac(ac_idx)
					for e in normalized_event_types:
						summary[e] += ac_summary.get(e, 0)
				st.markdown("### Event Totals for Planning Horizon (Fleet)")
			else:
				ac_idx = ac_options.index(selected_ac) - 1
				summary = count_events_for_ac(ac_idx)
				st.markdown(f"### Event Totals for Planning Horizon ({selected_ac})")

			# If unscheduled events are included, add them to the summary
			if _is_unscheduled_library_mode(sidebar_values.get("maintenance_mode")):
				unsched_path = os.path.join("data", "unscheduled_events.csv")
				if os.path.exists(unsched_path):
					unsched_df = pd.read_csv(unsched_path)
					annual_hours = sidebar_values["annual_hours_per_ac"]
					planning_years = sidebar_values["years"]
					for _, row in unsched_df.iterrows():
						event = row["Unscheduled Event"]
						n_events = _expected_unscheduled_events(annual_hours) * planning_years
						summary[event] = n_events * (fleet_size if selected_ac == "Fleet" else 1)
						event_lookup[event] = {
							"Man-Hours": float(row["Avg. Labour Hours"]) if not pd.isna(row["Avg. Labour Hours"]) else 0.0,
							"Parts $ / event": float(row["Adjusted Parts Cost"]) if not pd.isna(row["Adjusted Parts Cost"]) else 0.0,
							"Downtime": f"{row['Downtime']} day" if not pd.isna(row["Downtime"]) else ""
						}


			# Ensure all events from summary and event_lookup are included, and map normalized keys to original names for display
			all_event_keys = set(summary.keys()) | set([k.strip().lower() for k in event_lookup.keys()])
			# Build a mapping from normalized key to original event name (prefer event_name_map, fallback to event_lookup)
			display_event_names = []
			total_events_list = []
			forecast_mh = []
			forecast_parts = []
			forecast_downtime = []
			for e in all_event_keys:
				# Find the original event name for display
				orig_name = event_name_map.get(e)
				if not orig_name:
					# Try to find in event_lookup (for unscheduled events)
					for k in event_lookup.keys():
						if k.strip().lower() == e:
							orig_name = k
							break
				if not orig_name:
					orig_name = e  # fallback
				display_event_names.append(orig_name)
				n = summary.get(e, 0)
				vals = event_lookup.get(orig_name, event_lookup.get(e, {"Man-Hours": 0.0, "Parts $ / event": 0.0, "Downtime": ""}))
				total_events_list.append(n)
				forecast_mh.append(n * vals["Man-Hours"])
				contingency_multiplier = float(sidebar_values.get("geographic_contingency_multiplier", 1.0))
				forecast_parts.append(n * vals["Parts $ / event"] * contingency_multiplier)
				forecast_downtime.append(forecast_downtime_days(vals["Downtime"], n))

			summary_table = pd.DataFrame({
				"Event Type": display_event_names,
				"Total Events": total_events_list,
				"Forecast Man-Hours": forecast_mh,
				"Forecast Parts Cost": forecast_parts,
				"Forecast Downtime": forecast_downtime
			})
			summary_table["Total Events"] = (
				pd.to_numeric(summary_table["Total Events"], errors="coerce")
				.fillna(0)
				.round(0)
				.astype(int)
			)
			# Only show events with Total Events > 0
			summary_table = summary_table[summary_table["Total Events"] > 0].reset_index(drop=True)

			# Sort by first due event (PMI 1 first, then PMI 2, then by interval)
			def event_sort_key(event):
				if event == "PMI 1": return 0
				if event == "PMI 2": return 1
				try:
					interval = float(scheduled_df[scheduled_df["Scheduled Event"] == event]["Interval (hrs)"].values[0])
				except:
					interval = 99999
				return 2 + interval
			summary_table = summary_table.sort_values(by="Event Type", key=lambda col: col.map(event_sort_key)).reset_index(drop=True)
			st.dataframe(summary_table)

		elif tabs[i] == "Availability & Downtime":
			scheduled_path = os.path.join("data", "scheduled_events.csv")
			if os.path.exists(scheduled_path):
				scheduled_df = pd.read_csv(scheduled_path)
			else:
				scheduled_df = pd.DataFrame()

			fleet_size = sidebar_values["fleet_size"]
			event_types = scheduled_df["Scheduled Event"].dropna().unique().tolist()
			event_types = [e for e in event_types if e not in ("Daily Pre-flight", "")]
			aircraft_order = [f"Aircraft {idx+1}" for idx in range(fleet_size)]
			palette = px.colors.qualitative.Plotly
			aircraft_color_map = {
				name: palette[idx % len(palette)]
				for idx, name in enumerate(aircraft_order)
			}

			st.header("Availability Over Contract")

			def parse_downtime_days(value):
				if pd.isna(value):
					return 0.0
				s = str(value).strip().lower()
				if s == "":
					return 0.0
				try:
					return float(s.split()[0])
				except Exception:
					try:
						return float(s)
					except Exception:
						return 0.0

			@st.cache_data(show_spinner=False)
			def build_availability_for_fleet(fleet_size, sidebar_values, scheduled_df):
				planning_start_date = pd.to_datetime(sidebar_values["planning_start_date"])
				planning_end_date = planning_start_date + pd.DateOffset(years=sidebar_values["years"])
				month_starts = [
					d for d in pd.date_range(start=planning_start_date.replace(day=1), end=planning_end_date, freq="MS")
					if d < planning_end_date
				]

				scheduled_downtime_lookup = {}
				for _, row in scheduled_df.iterrows():
					event = row.get("Scheduled Event", "")
					if not isinstance(event, str):
						continue
					event_key = event.strip().lower()
					scheduled_downtime_lookup[event_key] = parse_downtime_days(row.get("Downtime", 0.0))

				unsched_downtime_days = []
				if _is_unscheduled_library_mode(sidebar_values.get("maintenance_mode")):
					unsched_path = os.path.join("data", "unscheduled_events.csv")
					if os.path.exists(unsched_path):
						unsched_df = pd.read_csv(unsched_path)
						unsched_downtime_days = [parse_downtime_days(row.get("Downtime", 0.0)) for _, row in unsched_df.iterrows()]

				records = []
				for ac_idx in range(fleet_size):
					if sidebar_values.get("use_custom_ac_dates") and len(sidebar_values.get("custom_ac_dates", [])) > ac_idx:
						ac_start_date = pd.to_datetime(sidebar_values["custom_ac_dates"][ac_idx])
					else:
						ac_start_date = planning_start_date

					ac_end_date = min(ac_start_date + pd.DateOffset(years=sidebar_values["years"]), planning_end_date)
					if ac_end_date <= planning_start_date:
						continue

					annual_hours = float(sidebar_values["annual_hours_per_ac"])
					fh = float(sidebar_values["hours_until_pmi"][ac_idx])
					contract_duration_years = (ac_end_date - ac_start_date).days / 365.25
					contract_fh = contract_duration_years * annual_hours
					contract_fh_end = contract_fh

					unavailable_by_month = {}

					def add_unavailable(event_date, downtime_days):
						if downtime_days <= 0:
							return
						if event_date < planning_start_date or event_date >= planning_end_date:
							return
						if event_date < ac_start_date or event_date >= ac_end_date:
							return
						m_start = pd.Timestamp(year=event_date.year, month=event_date.month, day=1)
						unavailable_by_month[m_start] = unavailable_by_month.get(m_start, 0.0) + downtime_days

					# PMI downtime events
					pmi_cycle = ["pmi 1", "pmi 2"]
					pmi_idx = 0
					first_pmi_offset = fh if fh > 0 else 480.0
					next_pmi_fh = first_pmi_offset
					while next_pmi_fh < contract_fh_end:
						event_key = pmi_cycle[pmi_idx % 2]
						years_since_start = next_pmi_fh / annual_hours if annual_hours > 0 else 0
						event_date = ac_start_date + pd.DateOffset(days=int(years_since_start * 365.25))
						add_unavailable(event_date, scheduled_downtime_lookup.get(event_key, 0.0))
						next_pmi_fh += 480
						pmi_idx += 1

					# Other scheduled downtime events
					for _, row in scheduled_df.iterrows():
						event = row.get("Scheduled Event", "")
						if not isinstance(event, str):
							continue
						event_key = event.strip().lower()
						if event_key in ("pmi 1", "pmi 2", "daily pre-flight", ""):
							continue
						downtime_days = scheduled_downtime_lookup.get(event_key, 0.0)
						if event_key == "90-day corrosion check":
							next_date = ac_start_date + pd.Timedelta(days=90)
							while next_date < ac_end_date:
								add_unavailable(next_date, downtime_days)
								next_date += pd.Timedelta(days=90)
						elif event_key == "6-month insp":
							next_date = ac_start_date + pd.Timedelta(days=182)
							while next_date < ac_end_date:
								add_unavailable(next_date, downtime_days)
								next_date += pd.Timedelta(days=182)
						elif event_key == "annual insp":
							next_date = ac_start_date + pd.Timedelta(days=365)
							while next_date < ac_end_date:
								add_unavailable(next_date, downtime_days)
								next_date += pd.Timedelta(days=365)
						else:
							interval_val = row.get("Interval (hrs)")
							if pd.notna(interval_val) and interval_val != "":
								interval = float(interval_val)
								if interval > 0:
									next_event_fh = fh + (interval - (fh % interval) if fh % interval != 0 else interval)
									while next_event_fh < contract_fh_end:
										years_since_start = (next_event_fh - fh) / annual_hours if annual_hours > 0 else 0
										event_date = ac_start_date + pd.DateOffset(days=int(years_since_start * 365.25))
										add_unavailable(event_date, downtime_days)
										next_event_fh += interval

					# Optional unscheduled downtime events
					n_events_unsched = _expected_unscheduled_events(annual_hours * contract_duration_years)
					if n_events_unsched > 0 and len(unsched_downtime_days) > 0:
						for downtime_days in unsched_downtime_days:
							event_date = ac_start_date + pd.DateOffset(days=int(((ac_end_date - ac_start_date).days) / 2))
							add_unavailable(event_date, downtime_days * n_events_unsched)

					for month_start in month_starts:
						month_end = month_start + pd.DateOffset(months=1)
						active_start = max(month_start, ac_start_date, planning_start_date)
						active_end = min(month_end, ac_end_date, planning_end_date)
						active_days = (active_end - active_start).days
						if active_days <= 0:
							continue
						unavailable_days = min(unavailable_by_month.get(month_start, 0.0), float(active_days))
						availability_pct = max(0.0, 100.0 * (1.0 - unavailable_days / active_days))
						records.append({
							"Aircraft": f"Aircraft {ac_idx+1}",
							"MonthStart": month_start,
							"ActiveDays": active_days,
							"UnavailableDays": unavailable_days,
							"AvailabilityPct": availability_pct,
						})

				return pd.DataFrame(records)

			availability_df = build_availability_for_fleet(fleet_size, sidebar_values, scheduled_df)
			target_availability = float(sidebar_values.get("target_availability", 75.0))
			availability_options = ["Fleet"] + [f"Aircraft {idx+1}" for idx in range(fleet_size)]
			selected_availability_view = st.selectbox("Availability View", availability_options, key="availability_view_select")

			if availability_df.empty:
				st.info("No availability data to display for the selected contract period.")
			else:
				if selected_availability_view == "Fleet":
					plot_df = availability_df.groupby("MonthStart", as_index=False).agg({"ActiveDays": "sum", "UnavailableDays": "sum"})
					plot_df = plot_df[plot_df["ActiveDays"] > 0].copy()
					plot_df["AvailabilityPct"] = 100.0 * (1.0 - plot_df["UnavailableDays"] / plot_df["ActiveDays"])
					plot_title = "Fleet Availability Over Contract"
				else:
					plot_df = availability_df[availability_df["Aircraft"] == selected_availability_view][["MonthStart", "AvailabilityPct"]].copy()
					plot_title = f"{selected_availability_view} Availability Over Contract"

				plot_df = plot_df.sort_values("MonthStart").copy()
				plot_df["MonthLabel"] = plot_df["MonthStart"].dt.strftime("%m/%Y")

				fig_availability = px.line(
					plot_df,
					x="MonthLabel",
					y="AvailabilityPct",
					markers=True,
					title=plot_title,
					labels={"MonthLabel": "Month (MM/YYYY)", "AvailabilityPct": "Availability (%)"}
				)
				if selected_availability_view != "Fleet":
					line_color = aircraft_color_map.get(selected_availability_view, "#1f77b4")
					fig_availability.update_traces(line_color=line_color, marker_color=line_color)
				fig_availability.add_hline(
					y=target_availability,
					line_dash="dash",
					line_color="red",
					annotation_text=f"Target {target_availability:.1f}%",
					annotation_position="top left"
				)
				fig_availability.update_yaxes(range=[0, 100])
				st.plotly_chart(
					fig_availability,
					use_container_width=True,
					config=_plotly_export_config("uh60_availability_over_contract"),
				)
				st.caption("Use the chart toolbar camera icon to export this graphic as PNG.")

			st.header("Per-Aircraft Maintenance Timeline")

			# For each aircraft, show a table of event dates and downtime
			@st.cache_data(show_spinner=False)
			def build_timeline_for_fleet(fleet_size, sidebar_values, scheduled_df):
				all_timeline_records = []
				mh_lookup = {}
				for _, row in scheduled_df.iterrows():
					event = row.get("Scheduled Event", "")
					if isinstance(event, str):
						mh_lookup[event.strip().lower()] = float(row.get("Man-Hours")) if pd.notna(row.get("Man-Hours")) else 0.0
				for ac_idx in range(fleet_size):
					timeline = []
					try:
						planning_start_date = pd.to_datetime(sidebar_values["planning_start_date"])
						planning_end_date = planning_start_date + pd.DateOffset(years=sidebar_values["years"])
						def in_display_window(dt):
							return planning_start_date <= dt < planning_end_date
						if sidebar_values.get("use_custom_ac_dates") and len(sidebar_values.get("custom_ac_dates", [])) > ac_idx:
							ac_start_date = pd.to_datetime(sidebar_values["custom_ac_dates"][ac_idx])
						else:
							ac_start_date = pd.to_datetime(sidebar_values["planning_start_date"])
						contract_end_date = ac_start_date + pd.DateOffset(years=sidebar_values["years"])
						contract_duration_years = (contract_end_date - ac_start_date).days / 365.25
						annual_hours = sidebar_values["annual_hours_per_ac"]
						fh = float(sidebar_values["hours_until_pmi"][ac_idx])
						pmi_cycle = ["PMI 1", "PMI 2"]
						pmi_idx = 0
						first_pmi_offset = fh if fh > 0 else 480.0
						contract_fh_end = contract_duration_years * annual_hours
						next_pmi_fh = first_pmi_offset
						while next_pmi_fh < contract_fh_end:
							event = pmi_cycle[pmi_idx % 2]
							event_key = event.strip().lower()
							years_since_start = next_pmi_fh / annual_hours if annual_hours > 0 else 0
							event_date = ac_start_date + pd.DateOffset(days=int(years_since_start * 365.25))
							if years_since_start >= contract_duration_years:
								break
							if event_date >= ac_start_date and in_display_window(event_date):
								manpower = mh_lookup.get(event_key, 0.0)
								months_since_start = (event_date.year - planning_start_date.year) * 12 + (event_date.month - planning_start_date.month)
								timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": event_key, "Date": event_date, "Month": months_since_start, "Manpower": manpower, "Type": "Scheduled"})
							next_pmi_fh += 480
							pmi_idx += 1
						for _, row in scheduled_df.iterrows():
							event = row["Scheduled Event"]
							if not isinstance(event, str):
								continue
							event_key = event.strip().lower()
							if event_key in ("pmi 1", "pmi 2", "daily pre-flight", ""):
								continue
							if event_key == "90-day corrosion check":
								interval_days = 90
								next_date = ac_start_date + pd.Timedelta(days=interval_days)
								while next_date < contract_end_date:
									years_since_start = (next_date - ac_start_date).days / 365.25
									if years_since_start >= contract_duration_years:
										break
									if next_date >= ac_start_date and in_display_window(next_date):
										manpower = row["Man-Hours"] if "Man-Hours" in row else 0
										months_since_start = (next_date.year - planning_start_date.year) * 12 + (next_date.month - planning_start_date.month)
										timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": event_key, "Date": next_date, "Month": months_since_start, "Manpower": manpower, "Type": "Scheduled"})
									next_date += pd.Timedelta(days=interval_days)
							elif event_key == "6-month insp":
								interval_days = 182
								next_date = ac_start_date + pd.Timedelta(days=interval_days)
								while next_date < contract_end_date:
									years_since_start = (next_date - ac_start_date).days / 365.25
									if years_since_start >= contract_duration_years:
										break
									if next_date >= ac_start_date and in_display_window(next_date):
										manpower = row["Man-Hours"] if "Man-Hours" in row else 0
										months_since_start = (next_date.year - planning_start_date.year) * 12 + (next_date.month - planning_start_date.month)
										timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": event_key, "Date": next_date, "Month": months_since_start, "Manpower": manpower, "Type": "Scheduled"})
									next_date += pd.Timedelta(days=interval_days)
							elif event_key == "annual insp":
								interval_days = 365
								next_date = ac_start_date + pd.Timedelta(days=interval_days)
								while next_date < contract_end_date:
									years_since_start = (next_date - ac_start_date).days / 365.25
									if years_since_start >= contract_duration_years:
										break
									if next_date >= ac_start_date and in_display_window(next_date):
										manpower = row["Man-Hours"] if "Man-Hours" in row else 0
										months_since_start = (next_date.year - planning_start_date.year) * 12 + (next_date.month - planning_start_date.month)
										timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": event_key, "Date": next_date, "Month": months_since_start, "Manpower": manpower, "Type": "Scheduled"})
									next_date += pd.Timedelta(days=interval_days)
							else:
								interval_val = row["Interval (hrs)"]
								if pd.notna(interval_val) and interval_val != '':
									interval = float(interval_val)
								else:
									interval = None
								if interval is not None and interval > 0:
									first_event_offset = interval - (fh % interval) if fh % interval != 0 else interval
									next_event_fh = fh + first_event_offset
									contract_fh_end = fh + contract_duration_years * annual_hours
									while next_event_fh < contract_fh_end:
										fh_since_start = next_event_fh - fh
										years_since_start = fh_since_start / annual_hours if annual_hours > 0 else 0
										if years_since_start >= contract_duration_years:
											break
										event_date = ac_start_date + pd.DateOffset(days=int(years_since_start * 365.25))
										if event_date >= ac_start_date and in_display_window(event_date):
											manpower = row["Man-Hours"] if "Man-Hours" in row else 0
											months_since_start = (event_date.year - planning_start_date.year) * 12 + (event_date.month - planning_start_date.month)
											timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": event_key, "Date": event_date, "Month": months_since_start, "Manpower": manpower, "Type": "Scheduled"})
										next_event_fh += interval
						if _is_unscheduled_library_mode(sidebar_values.get("maintenance_mode")):
							unsched_path = os.path.join("data", "unscheduled_events.csv")
							if os.path.exists(unsched_path):
								unsched_df = pd.read_csv(unsched_path)
								for _, row in unsched_df.iterrows():
									expected_events = _expected_unscheduled_events(annual_hours * contract_duration_years)
									if expected_events > 0:
										event_date = ac_start_date + pd.DateOffset(days=int((contract_end_date - ac_start_date).days / 2))
										if in_display_window(event_date):
											manpower = (row["Avg. Labour Hours"] if "Avg. Labour Hours" in row else 0) * expected_events
											months_since_start = (event_date.year - planning_start_date.year) * 12 + (event_date.month - planning_start_date.month)
											timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": row["Unscheduled Event"], "Date": event_date, "Month": months_since_start, "Manpower": manpower, "Type": "Unscheduled"})
						if ac_start_date > planning_start_date and ac_start_date < planning_end_date:
							timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": "Start Lag", "Date": pd.to_datetime(sidebar_values["planning_start_date"]), "Month": 0, "Manpower": 0, "Type": "Connector"})
							timeline.append({"Aircraft": f"Aircraft {ac_idx+1}", "Event": "Start Lag", "Date": ac_start_date, "Month": (ac_start_date.year - pd.to_datetime(sidebar_values["planning_start_date"]).year) * 12 + (ac_start_date.month - pd.to_datetime(sidebar_values["planning_start_date"]).month), "Manpower": 0, "Type": "Connector"})
					except Exception as e:
						st.warning(f"Unable to build timeline for Aircraft {ac_idx+1}: {e}")
						timeline = []
					all_timeline_records.extend(timeline)
				return pd.DataFrame(all_timeline_records)

			# Plot the graph for all aircraft
			if fleet_size > 0:
				all_timeline = build_timeline_for_fleet(fleet_size, sidebar_values, scheduled_df)
				plot_df = all_timeline[all_timeline["Type"] != "Connector"]
				agg_df = plot_df.groupby(["Month", "Aircraft", "Type", "Event"]).agg({"Manpower": "sum"}).reset_index()
				planning_start_date = pd.to_datetime(sidebar_values["planning_start_date"])
				planning_end_date = planning_start_date + pd.DateOffset(years=sidebar_values["years"])
				total_months = max((planning_end_date.year - planning_start_date.year) * 12 + (planning_end_date.month - planning_start_date.month), 1)
				all_months = list(range(total_months))
				month_labels = [(planning_start_date + pd.DateOffset(months=int(m))).strftime("%m/%Y") for m in all_months]
				month_map = dict(zip(all_months, month_labels))
				agg_df["MonthLabel"] = agg_df["Month"].map(month_map)
				aircraft_order = [f"Aircraft {idx+1}" for idx in range(fleet_size)]
				palette = px.colors.qualitative.Plotly
				aircraft_color_map = {
					name: palette[idx % len(palette)]
					for idx, name in enumerate(aircraft_order)
				}
				fig = px.bar(
					agg_df,
					x="MonthLabel",
					y="Manpower",
					color="Aircraft",
					pattern_shape="Type",
					color_discrete_map=aircraft_color_map,
					category_orders={"Aircraft": aircraft_order, "Type": ["Scheduled", "Unscheduled"]},
					barmode="stack",
					hover_data=["Event", "Type", "Month"],
					labels={"MonthLabel": "Month (MM/YYYY)", "Manpower": "Total Manpower Required", "Event": "Event Type", "Type": "Maintenance Type"},
					title="Stacked Maintenance Manpower by Month and Aircraft",
					text_auto=True
				)
				fig.update_layout(
					legend=dict(
						orientation="h",
						yanchor="top",
						y=-0.25,
						xanchor="left",
						x=0
					),
					xaxis=dict(
						categoryorder="array",
						categoryarray=month_labels
					),
					margin=dict(b=120)
				)
				seen_aircraft = set()
				for trace in fig.data:
					aircraft_name = str(trace.name).split(",")[0].strip()
					trace.legendgroup = aircraft_name
					if aircraft_name in seen_aircraft:
						trace.showlegend = False
					else:
						trace.name = aircraft_name
						trace.showlegend = True
						seen_aircraft.add(aircraft_name)
				start_label_counts = {}
				for ac_idx in range(fleet_size):
					aircraft_name = f"Aircraft {ac_idx+1}"
					ac_color = aircraft_color_map.get(aircraft_name, "gray")
					ac_df = all_timeline[(all_timeline["Aircraft"] == aircraft_name) & (all_timeline["Type"] == "Connector")].sort_values("Date")
					if len(ac_df) == 2:
						x0_label = month_map.get(ac_df.iloc[0]["Month"])
						x1_label = month_map.get(ac_df.iloc[1]["Month"])
						if x0_label is not None and x1_label is not None:
							fig.add_shape(type="line",
							x0=x0_label, y0=0,
							x1=x1_label, y1=0,
							line=dict(color="gray", dash="dot"),
							xref="x", yref="y"
						)
					if sidebar_values.get("use_custom_ac_dates") and len(sidebar_values.get("custom_ac_dates", [])) > ac_idx:
						ac_start_date = pd.to_datetime(sidebar_values["custom_ac_dates"][ac_idx])
					else:
						ac_start_date = pd.to_datetime(sidebar_values["planning_start_date"])
					start_month_label = ac_start_date.strftime("%m/%Y")
					label_idx = start_label_counts.get(start_month_label, 0)
					start_label_counts[start_month_label] = label_idx + 1
					label_ay = -40 - (label_idx * 14)
					label_ax = (20 + (label_idx // 2) * 12) * (1 if label_idx % 2 == 0 else -1)
					fig.add_vline(
						x=start_month_label,
						line_width=2,
						line_dash="dash",
						line_color=ac_color
					)
					fig.add_annotation(
						x=start_month_label,
						y=0,
						text=f"AC{ac_idx+1} Start",
						showarrow=True,
						arrowhead=1,
						ax=label_ax,
						ay=label_ay,
						font=dict(color=ac_color, size=12),
						bgcolor="white",
						bordercolor=ac_color,
						borderwidth=1
					)
				st.plotly_chart(
					fig,
					use_container_width=True,
					config=_plotly_export_config("uh60_maintenance_timeline"),
				)
				st.caption("Use the chart toolbar camera icon to export this graphic as PNG.")
		elif tabs[i] == "Costings":
			st.header("Costings")
			apply_escalation = st.toggle("Apply Annual Escalation", value=True, key="costings_escalation_toggle")
			st.markdown("### MRO Operating Costs (Annual)")
			st.caption(
				"Add fixed annual costs to run the MRO capability. These are included in annual and contract totals. "
				"Escalation and geographic contingency are applied in the same way as other customer-facing costs."
			)
			with st.expander("Operating Cost Inputs", expanded=True):
				# Build preset profiles from the centralized function
				mro_level_presets = _get_mro_level_presets()
				preset_profiles = {
					"Custom": None,
					"Lean MRO": mro_level_presets["Lean"],
					"Standard MRO": mro_level_presets["Standard"],
					"Full Capability MRO": mro_level_presets["Full"],
				}
				preset_col1, preset_col2 = st.columns([3, 1])
				with preset_col1:
					selected_preset = st.selectbox(
						"Overhead preset",
						list(preset_profiles.keys()),
						index=0,
						key="mro_overhead_preset_select",
						help="Choose a baseline profile and click Load Preset to populate all annual overhead fields.",
					)
					scale_preset_with_fleet = st.toggle(
						"Scale preset with fleet size",
						value=True,
						key="mro_overhead_preset_scale_toggle",
						help="If enabled, each category uses Fixed + (Per-Aircraft x Fleet Size).",
					)
				with preset_col2:
					st.write("")
					st.write("")
					if st.button("Load Preset", key="load_mro_overhead_preset"):
						selected_values = preset_profiles.get(selected_preset)
						if selected_values:
							for preset_key, components in selected_values.items():
								fixed_component = float(components.get("fixed", 0.0))
								per_ac_component = float(components.get("per_ac", 0.0))
								if scale_preset_with_fleet:
									preset_value = fixed_component + (per_ac_component * float(sidebar_values.get("fleet_size", 1)))
								else:
									reference_fleet = 12.0
									preset_value = fixed_component + (per_ac_component * reference_fleet)
								st.session_state[preset_key] = float(preset_value)
							st.success(f"Loaded preset: {selected_preset}")
							st.rerun()
				preset_values = preset_profiles.get(selected_preset)
				if preset_values:
					if scale_preset_with_fleet:
						preview_total = sum(
							float(v.get("fixed", 0.0)) + (float(v.get("per_ac", 0.0)) * float(sidebar_values.get("fleet_size", 1)))
							for v in preset_values.values()
						)
						preview_basis = f"fleet size {int(sidebar_values.get('fleet_size', 1))}"
					else:
						reference_fleet = 12.0
						preview_total = sum(
							float(v.get("fixed", 0.0)) + (float(v.get("per_ac", 0.0)) * reference_fleet)
							for v in preset_values.values()
						)
						preview_basis = "reference fleet size 12"
					display_factor_for_preview = _display_conversion_factor(sidebar_values)
					st.caption(
						f"Preset annual overhead total ({preview_basis}): "
							f"{sidebar_values['currency_symbol']}{(preview_total * display_factor_for_preview):,.0f} "
						"(before escalation and contingency)."
					)

				o1, o2, o3 = st.columns(3)
				with o1:
					mro_cost_insurance = st.number_input(
						"Insurance (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_insurance", 0.0)),
						step=1000.0,
						key="mro_cost_insurance",
					)
					mro_cost_facility = st.number_input(
						"Facility Costs (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_facility", 0.0)),
						step=1000.0,
						key="mro_cost_facility",
					)
					mro_cost_gse = st.number_input(
						"GSE (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_gse", 0.0)),
						step=1000.0,
						key="mro_cost_gse",
					)
				with o2:
					mro_cost_tooling = st.number_input(
						"UH-60 Specialist Tooling (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_tooling", 0.0)),
						step=1000.0,
						key="mro_cost_tooling",
					)
					mro_cost_engine_bay = st.number_input(
						"Engine Bay (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_engine_bay", 0.0)),
						step=1000.0,
						key="mro_cost_engine_bay",
					)
					mro_cost_rotables_store = st.number_input(
						"Rotables Store (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_rotables_store", 0.0)),
						step=1000.0,
						key="mro_cost_rotables_store",
					)
				with o3:
					mro_cost_parts_store = st.number_input(
						"Parts Store (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_parts_store", 0.0)),
						step=1000.0,
						key="mro_cost_parts_store",
					)
					mro_cost_utilities = st.number_input(
						"Utilities (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_utilities", 0.0)),
						step=1000.0,
						key="mro_cost_utilities",
					)
					mro_cost_it_quality = st.number_input(
						"IT/Quality/Compliance (USD/year)",
						min_value=0.0,
						value=float(st.session_state.get("mro_cost_it_quality", 0.0)),
						step=1000.0,
						key="mro_cost_it_quality",
					)

			costings_inputs = dict(sidebar_values)
			costings_inputs.update({
				"mro_cost_insurance": mro_cost_insurance,
				"mro_cost_facility": mro_cost_facility,
				"mro_cost_gse": mro_cost_gse,
				"mro_cost_tooling": mro_cost_tooling,
				"mro_cost_engine_bay": mro_cost_engine_bay,
				"mro_cost_rotables_store": mro_cost_rotables_store,
				"mro_cost_parts_store": mro_cost_parts_store,
				"mro_cost_utilities": mro_cost_utilities,
				"mro_cost_it_quality": mro_cost_it_quality,
			})

			costings_df = _build_costings_dataframe(costings_inputs, apply_escalation=apply_escalation)
			display_factor = _display_conversion_factor(sidebar_values)
			n_years = int(sidebar_values["years"])
			annual_hours = float(sidebar_values["annual_hours_per_ac"])
			fleet_size = int(sidebar_values["fleet_size"])
			contingency_multiplier = float(sidebar_values.get("geographic_contingency_multiplier", 1.0))
			annual_management_fee = float(sidebar_values.get("annual_management_fee_per_ac", 0.0)) * fleet_size
			labour_rate = float(sidebar_values.get("labour_rate", 0.0))
			overhead_categories = [label for label, _ in _mro_overhead_categories()]
			cost_columns = [
				"Manpower Cost",
				"Parts Cost",
				"Management Fee",
				"MRO Overheads",
				"Total Cost",
			] + [c for c in overhead_categories if c in costings_df.columns]
			annual_costings_display_df = costings_df.copy()
			currency_cols_in_display = [col for col in cost_columns if col in annual_costings_display_df.columns]
			if currency_cols_in_display:
				annual_costings_display_df[currency_cols_in_display] = annual_costings_display_df[currency_cols_in_display] * display_factor
			numeric_cols = annual_costings_display_df.select_dtypes(include=["number"]).columns.tolist()
			contract_fh_total_for_table = float(costings_df["Total FH"].sum()) if "Total FH" in costings_df.columns else 0.0
			if contract_fh_total_for_table > 0:
				fh_cost_row = {
					"Contract Year": float("nan"),
					"Period": "FH Cost",
				}
				for col in numeric_cols:
					if col == "Contract Year":
						continue
					fh_cost_row[col] = float(costings_df[col].sum()) / contract_fh_total_for_table
				annual_costings_display_df = pd.concat([annual_costings_display_df, pd.DataFrame([fh_cost_row])], ignore_index=True)
			if numeric_cols:
				annual_costings_display_df[numeric_cols] = annual_costings_display_df[numeric_cols].round(0)
			st.markdown("### Annual Costings by Contract Year")
			st.caption("USD calculation base; values shown in selected display currency using current FX rate.")
			st.dataframe(
				annual_costings_display_df.style.format({
					**{col: "{:.0f}" for col in ["Contract Year", "Total FH", "Manpower Hrs"] if col in annual_costings_display_df.columns},
					**{col: f"{sidebar_values['currency_symbol']}{{:,.0f}}" for col in cost_columns if col in annual_costings_display_df.columns},
				}, na_rep="")
			)
			if contract_fh_total_for_table > 0:
				component_items = ["Manpower Cost", "Parts Cost", "Management Fee", "MRO Overheads"]
				present_components = [item for item in component_items if item in costings_df.columns]
				if present_components:
					component_values = [
						(float(costings_df[item].sum()) / contract_fh_total_for_table) * display_factor
						for item in present_components
					]
					line_x = present_components + ["Total Cost"]
					line_y = []
					running_total = 0.0
					for val in component_values:
						running_total += val
						line_y.append(running_total)
					line_y.append(running_total)

					st.markdown("#### Annual Costings by Contract Year: FH Cost by Costing Item")
					fh_fig = go.Figure()
					fh_fig.add_trace(go.Bar(
						x=present_components,
						y=component_values,
						name="Cost Components",
						text=[f"{sidebar_values['currency_symbol']}{v:,.0f}" for v in component_values],
						textposition="outside",
					))
					fh_fig.add_trace(go.Scatter(
						x=line_x,
						y=line_y,
						mode="lines+markers+text",
						name="Total Cost (Cumulative)",
						line={"width": 3},
						text=[f"{sidebar_values['currency_symbol']}{v:,.0f}" for v in line_y],
						textposition="top center",
					))
					fh_fig.update_layout(
						yaxis_tickformat=",.0f",
						xaxis_title="Costing Item",
						yaxis_title=f"FH Cost ({sidebar_values['currency_symbol']})",
					)
					st.plotly_chart(
						fh_fig,
						use_container_width=True,
						config=_plotly_export_config("annual_costings_fh_cost_by_item"),
					)
			overhead_cols_present = [c for c in overhead_categories if c in costings_df.columns]
			if overhead_cols_present:
				st.markdown("#### Annual MRO Overheads Breakdown")
				st.caption("USD calculation base; values shown in selected display currency using current FX rate.")
				overhead_breakdown_df = costings_df[["Contract Year", "Period"] + overhead_cols_present + ["MRO Overheads"]]
				overhead_breakdown_df[overhead_cols_present + ["MRO Overheads"]] = overhead_breakdown_df[overhead_cols_present + ["MRO Overheads"]] * display_factor
				st.dataframe(
					overhead_breakdown_df.style.format({
						col: f"{sidebar_values['currency_symbol']}{{:,.0f}}"
						for col in overhead_cols_present + ["MRO Overheads"]
					}),
					use_container_width=True,
				)
				if contract_fh_total_for_table > 0:
					overhead_component_values = [
						(float(costings_df[col].sum()) / contract_fh_total_for_table) * display_factor
						for col in overhead_cols_present
					]
					overhead_line_x = overhead_cols_present + ["MRO Overheads"]
					overhead_line_y = []
					overhead_running_total = 0.0
					for val in overhead_component_values:
						overhead_running_total += val
						overhead_line_y.append(overhead_running_total)
					overhead_line_y.append(overhead_running_total)

					st.markdown("##### FH Cost by Overhead Item (Cumulative Total Line)")
					overhead_fig = go.Figure()
					overhead_fig.add_trace(go.Bar(
						x=overhead_cols_present,
						y=overhead_component_values,
						name="Overhead Components",
						text=[f"{sidebar_values['currency_symbol']}{v:,.0f}" for v in overhead_component_values],
						textposition="outside",
					))
					overhead_fig.add_trace(go.Scatter(
						x=overhead_line_x,
						y=overhead_line_y,
						mode="lines+markers+text",
						name="MRO Overheads (Cumulative)",
						line={"width": 3},
						text=[f"{sidebar_values['currency_symbol']}{v:,.0f}" for v in overhead_line_y],
						textposition="top center",
					))
					overhead_fig.update_layout(
						yaxis_tickformat=",.0f",
						xaxis_title="Overhead Item",
						yaxis_title=f"FH Cost ({sidebar_values['currency_symbol']})",
					)
					st.plotly_chart(
						overhead_fig,
						use_container_width=True,
						config=_plotly_export_config("annual_mro_overheads_fh_cost_by_item"),
					)

			c1, c2, c3, c4 = st.columns(4)
			contract_fh_total = costings_df["Total FH"].sum()
			contract_manpower_hours_total = costings_df["Manpower Hrs"].sum()
			manpower_cost_per_fh = (costings_df["Manpower Cost"].sum() / contract_fh_total) if contract_fh_total > 0 else 0.0
			parts_cost_per_fh = (costings_df["Parts Cost"].sum() / contract_fh_total) if contract_fh_total > 0 else 0.0
			overheads_cost_per_fh = (costings_df["MRO Overheads"].sum() / contract_fh_total) if contract_fh_total > 0 else 0.0
			contract_management_fee = float(costings_df["Management Fee"].sum()) if "Management Fee" in costings_df.columns else (annual_management_fee * n_years)
			annual_management_fee = (contract_management_fee / n_years) if n_years > 0 else 0.0
			management_fee_per_fh = (contract_management_fee / contract_fh_total) if contract_fh_total > 0 else 0.0
			contract_overheads = float(costings_df["MRO Overheads"].sum())
			annual_overheads = contract_overheads / n_years if n_years > 0 else 0.0
			labour_cost = float(sidebar_values.get("labour_cost", 0.0))
			contract_manpower_cost_delta = (labour_rate - labour_cost) * contract_manpower_hours_total * contingency_multiplier
			manpower_delta_per_fh = (((labour_rate - labour_cost) * contract_manpower_hours_total * contingency_multiplier) / contract_fh_total) if contract_fh_total > 0 else 0.0
			with c1:
				st.metric("Contract FH", f"{costings_df['Total FH'].sum():.0f}")
				st.metric("Contract Management Fee", f"{sidebar_values['currency_symbol']}{(contract_management_fee * display_factor):,.0f}")
				st.metric("Annual Management Fee", f"{sidebar_values['currency_symbol']}{(annual_management_fee * display_factor):,.0f}")
				st.metric("Management Fee / FH", f"{sidebar_values['currency_symbol']}{(management_fee_per_fh * display_factor):,.0f}")
			with c2:
				st.metric("Contract Manpower Hrs", f"{costings_df['Manpower Hrs'].sum():.0f}")
			with c3:
				st.metric("Contract Manpower Cost", f"{sidebar_values['currency_symbol']}{(costings_df['Manpower Cost'].sum() * display_factor):,.0f}")
				st.metric("Contract Manpower Cost Delta", f"{sidebar_values['currency_symbol']}{(contract_manpower_cost_delta * display_factor):,.0f}")
				st.metric("Manpower Cost / FH", f"{sidebar_values['currency_symbol']}{(manpower_cost_per_fh * display_factor):,.0f}")
				st.metric("Manpower Delta /FH", f"{sidebar_values['currency_symbol']}{(manpower_delta_per_fh * display_factor):,.0f}")
			with c4:
				st.metric("Contract Parts Cost", f"{sidebar_values['currency_symbol']}{(costings_df['Parts Cost'].sum() * display_factor):,.0f}")
				st.metric("Parts Cost /FH", f"{sidebar_values['currency_symbol']}{(parts_cost_per_fh * display_factor):,.0f}")
				st.metric("Contract MRO Overheads", f"{sidebar_values['currency_symbol']}{(contract_overheads * display_factor):,.0f}")
				st.metric("Annual MRO Overheads", f"{sidebar_values['currency_symbol']}{(annual_overheads * display_factor):,.0f}")
				st.metric("MRO Overheads /FH", f"{sidebar_values['currency_symbol']}{(overheads_cost_per_fh * display_factor):,.0f}")

			# ----------------------------------------------------------------
			# MRO Manpower Planning & Staffing Estimate
			# ----------------------------------------------------------------
			st.markdown("---")
			st.markdown("### MRO Manpower Planning & Staffing Estimate")
			st.caption(
				"Derives the number of engineers, mechanics, QC inspectors and support staff "
				"required to sustain MRO operations for the modelled fleet, annual flying rate and contract length."
			)

			with st.expander("Staffing Assumptions", expanded=True):
				mpc1, mpc2, mpc3 = st.columns(3)
				with mpc1:
					mp_productive_hrs = st.number_input(
						"Productive hours per person per year",
						min_value=500, max_value=2500, value=1500, step=50,
						key="mp_productive_hrs",
						help="Net annual working hours after annual leave, public holidays, training and sick days (typically 1,400–1,800 hrs).",
					)
					mp_engineer_ratio = st.number_input(
						"Mechanics per Licensed Aircraft Engineer (LAE)",
						min_value=1, max_value=10, value=3, step=1,
						key="mp_engineer_ratio",
						help="The number of aircraft mechanics supported/signed-off by one Licensed Aircraft Engineer.",
					)
				with mpc2:
					mp_qc_ratio = st.number_input(
						"Direct maintainers per QC/QA Inspector",
						min_value=1, max_value=20, value=8, step=1,
						key="mp_qc_ratio",
						help="One Quality Control/Assurance Inspector for every N direct maintenance staff.",
					)
					mp_parts_ratio = st.number_input(
						"Aircraft per Parts/Logistics Coordinator",
						min_value=1, max_value=20, value=4, step=1,
						key="mp_parts_ratio",
						help="One supply-chain and parts coordinator per N aircraft in the fleet.",
					)
				with mpc3:
					mp_include_planning = st.toggle(
						"Include Planning & Engineering Officer",
						value=True, key="mp_include_planning",
						help="Adds one dedicated planning/production-control engineer to the MRO team.",
					)
					mp_shift_coverage = st.selectbox(
						"Operational shift coverage",
						["Single shift (1×)", "Double shift (2×)"],
						key="mp_shift_coverage",
						help="Double shift doubles the direct maintenance headcount to maintain continuous operations.",
					)

			shift_mult = 2.0 if mp_shift_coverage.startswith("Double") else 1.0
			annual_avg_mh = (costings_df["Manpower Hrs"].sum() / n_years) if n_years > 0 else 0.0
			cs = sidebar_values["currency_symbol"]
			labour_cost_rate = float(sidebar_values.get("labour_cost", 0.0))

			# Direct maintenance headcount (to deliver the modelled manpower hours)
			direct_staff_raw = (annual_avg_mh / mp_productive_hrs * shift_mult) if mp_productive_hrs > 0 else 0.0
			direct_staff = max(1, math.ceil(direct_staff_raw)) if annual_avg_mh > 0 else 0

			# LAE / mechanic split: engineer_ratio mechanics per 1 LAE
			lae_count = max(1, math.ceil(direct_staff / (mp_engineer_ratio + 1))) if direct_staff > 0 else 0
			mechanic_count = max(0, direct_staff - lae_count)

			# Support roles
			qc_count = max(1, math.ceil(direct_staff / mp_qc_ratio)) if direct_staff > 0 else 1
			parts_count = max(1, math.ceil(fleet_size / mp_parts_ratio))
			planning_count = 1 if mp_include_planning else 0
			total_staff = direct_staff + qc_count + parts_count + planning_count

			# Utilisation of direct maintenance staff
			annual_available_mh = total_staff * mp_productive_hrs
			utilisation_pct = (
				(annual_avg_mh / (direct_staff * mp_productive_hrs) * 100.0)
				if direct_staff * mp_productive_hrs > 0 else 0.0
			)

			# Annual cost and charge
			annual_staff_cost = total_staff * mp_productive_hrs * labour_cost_rate
			annual_charge = total_staff * mp_productive_hrs * labour_rate * contingency_multiplier

			# Staffing breakdown table
			def _staffing_row(role, count):
				return {
					"Role": role,
					"Headcount": count,
					"Annual Hrs Available": count * mp_productive_hrs,
					"Annual Staff Cost": count * mp_productive_hrs * labour_cost_rate,
					"Annual MRO Charge": count * mp_productive_hrs * labour_rate * contingency_multiplier,
				}

			staffing_rows = [
				_staffing_row("Licensed Aircraft Engineer (LAE)", lae_count),
				_staffing_row("Aircraft Mechanic", mechanic_count),
				_staffing_row("QC / Quality Assurance Inspector", qc_count),
				_staffing_row("Parts / Logistics Coordinator", parts_count),
			]
			if planning_count:
				staffing_rows.append(_staffing_row("Planning & Engineering Officer", planning_count))
			staffing_rows.append({
				"Role": "TOTAL",
				"Headcount": total_staff,
				"Annual Hrs Available": annual_available_mh,
				"Annual Staff Cost": annual_staff_cost,
				"Annual MRO Charge": annual_charge,
			})
			staffing_df = pd.DataFrame(staffing_rows)
			if not staffing_df.empty:
				staffing_df[["Annual Staff Cost", "Annual MRO Charge"]] = staffing_df[["Annual Staff Cost", "Annual MRO Charge"]] * display_factor

			st.markdown("#### Estimated MRO Staffing (Annual Average)")
			st.caption("USD calculation base; values shown in selected display currency using current FX rate.")
			st.dataframe(
				staffing_df.style.format({
					"Headcount": "{:.0f}",
					"Annual Hrs Available": "{:,.0f}",
					"Annual Staff Cost": f"{cs}{{:,.0f}}",
					"Annual MRO Charge": f"{cs}{{:,.0f}}",
				}),
				use_container_width=True,
			)

			# KPI metrics row
			km1, km2, km3, km4, km5 = st.columns(5)
			with km1:
				st.metric("Total Headcount", f"{total_staff}")
			with km2:
				st.metric("Direct Maintenance Staff", f"{direct_staff}")
			with km3:
				st.metric(
					"Direct Staff Utilisation",
					f"{utilisation_pct:.0f}%",
					help="Modelled maintenance hours ÷ total available direct staff hours.",
				)
			with km4:
				st.metric("Annual Staff Cost", f"{cs}{(annual_staff_cost * display_factor):,.0f}")
			with km5:
				st.metric("Annual MRO Labour Charge", f"{cs}{(annual_charge * display_factor):,.0f}")

			if utilisation_pct > 100.0:
				st.warning(
					f"Direct staff utilisation is {utilisation_pct:.0f}% — modelled maintenance hours exceed "
					"available direct staff capacity. Consider increasing headcount or adding a second shift."
				)
			elif utilisation_pct > 85.0:
				st.info(f"Direct staff utilisation is {utilisation_pct:.0f}% — operating near capacity.")

			# Per-contract-year staffing table
			st.markdown("#### Staffing Requirement by Contract Year")
			st.caption("USD calculation base; values shown in selected display currency using current FX rate.")
			year_staffing_rows = []
			for _, yr_row in costings_df.iterrows():
				yr_mh = float(yr_row["Manpower Hrs"])
				yr_direct_raw = (yr_mh / mp_productive_hrs * shift_mult) if mp_productive_hrs > 0 else 0.0
				yr_direct = max(1, math.ceil(yr_direct_raw)) if yr_mh > 0 else 0
				yr_lae = max(1, math.ceil(yr_direct / (mp_engineer_ratio + 1))) if yr_direct > 0 else 0
				yr_mech = max(0, yr_direct - yr_lae)
				yr_qc = max(1, math.ceil(yr_direct / mp_qc_ratio)) if yr_direct > 0 else 1
				yr_parts = max(1, math.ceil(fleet_size / mp_parts_ratio))
				yr_planning = planning_count
				yr_total = yr_direct + yr_qc + yr_parts + yr_planning
				yr_util = (
					(yr_mh / (yr_direct * mp_productive_hrs) * 100.0)
					if yr_direct * mp_productive_hrs > 0 else 0.0
				)
				yr_staff_cost = yr_total * mp_productive_hrs * labour_cost_rate
				yr_charge = yr_total * mp_productive_hrs * labour_rate * contingency_multiplier
				year_staffing_rows.append({
					"Contract Year": int(yr_row["Contract Year"]),
					"Period": yr_row["Period"],
					"Maint. Hrs": yr_mh,
					"LAE": yr_lae,
					"Mechanics": yr_mech,
					"QC": yr_qc,
					"Parts/Logs": yr_parts,
					"Planning": yr_planning,
					"Total Staff": yr_total,
					"Utilisation %": round(yr_util, 1),
					"Staff Cost": yr_staff_cost,
					"MRO Labour Charge": yr_charge,
				})

			year_staffing_df = pd.DataFrame(year_staffing_rows)
			if not year_staffing_df.empty:
				year_staffing_df[["Staff Cost", "MRO Labour Charge"]] = year_staffing_df[["Staff Cost", "MRO Labour Charge"]] * display_factor
			st.dataframe(
				year_staffing_df.style.format({
					"Maint. Hrs": "{:,.0f}",
					"LAE": "{:.0f}",
					"Mechanics": "{:.0f}",
					"QC": "{:.0f}",
					"Parts/Logs": "{:.0f}",
					"Planning": "{:.0f}",
					"Total Staff": "{:.0f}",
					"Utilisation %": "{:.0f}",
					"Staff Cost": f"{cs}{{:,.0f}}",
					"MRO Labour Charge": f"{cs}{{:,.0f}}",
				}),
				use_container_width=True,
			)
			st.caption(
				"**Staff Cost** uses the Labour Cost/hr input (internal employment cost). "
				"**MRO Labour Charge** uses the Labour Rate/hr input with geographic contingency applied (customer-facing charge). "
				"Direct headcount is derived from modelled maintenance hours ÷ productive hours per person. "
				"QC, Parts/Logistics and Planning roles are additional to direct maintenance staff."
			)

			# MRO Levels Comparison
			st.markdown("---")
			st.subheader("MRO Capability Levels Comparison")
			st.caption(
				"Compare total contract costs across the three MRO capability levels (Lean, Standard, Full) "
				"for each maintenance approach. This helps identify the cost impact of different MRO investment levels."
			)

			try:
				mro_comparison_df = _calculate_mro_levels_comparison(sidebar_values)

				# Create pivot table for easier viewing
				display_factor = _display_conversion_factor(sidebar_values)
				currency_symbol = sidebar_values.get("currency_symbol", "$")

				# Create summary table - Total Cost by Maintenance Approach and MRO Level
				cost_pivot = mro_comparison_df.pivot_table(
					values="Total Cost",
					index="MRO Level",
					columns="Maintenance Approach",
					aggfunc="first"
				)
				cost_per_fh_pivot = mro_comparison_df.pivot_table(
					values="Cost/FH",
					index="MRO Level",
					columns="Maintenance Approach",
					aggfunc="first"
				)
				annual_cost_pivot = mro_comparison_df.pivot_table(
					values="Annual Avg Cost",
					index="MRO Level",
					columns="Maintenance Approach",
					aggfunc="first"
				)

				# Display tables
				col1, col2 = st.columns(2)

				with col1:
					st.write("**Total Contract Cost**")
					cost_display = cost_pivot * display_factor
					st.dataframe(
						cost_display.map(lambda x: f"{currency_symbol}{x:,.0f}" if pd.notna(x) else ""),
						use_container_width=True,
					)

				with col2:
					st.write("**Cost per Flight Hour**")
					cost_fh_display = cost_per_fh_pivot * display_factor
					st.dataframe(
						cost_fh_display.map(lambda x: f"{currency_symbol}{x:,.2f}" if pd.notna(x) else ""),
						use_container_width=True,
					)

				st.write("**Average Annual Cost**")
				annual_display = annual_cost_pivot * display_factor
				st.dataframe(
					annual_display.map(lambda x: f"{currency_symbol}{x:,.0f}" if pd.notna(x) else ""),
					use_container_width=True,
				)

				# Create visualization
				fig = go.Figure()

				# Reorder for better visualization
				mro_order = ["Lean", "Standard", "Full"]
				mro_comparison_df["MRO Level"] = pd.Categorical(
					mro_comparison_df["MRO Level"],
					categories=mro_order,
					ordered=True
				)
				mro_comparison_df_sorted = mro_comparison_df.sort_values("MRO Level")

				# Create grouped bar chart
				for approach in mro_comparison_df_sorted["Maintenance Approach"].unique():
					approach_data = mro_comparison_df_sorted[mro_comparison_df_sorted["Maintenance Approach"] == approach]
					fig.add_trace(go.Bar(
						x=approach_data["MRO Level"],
						y=approach_data["Total Cost"] * display_factor,
						name=approach,
						text=[f"{currency_symbol}{v * display_factor:,.0f}" for v in approach_data["Total Cost"]],
						textposition="outside",
						hovertemplate="<b>%{name}</b><br>MRO Level: %{x}<br>Total Cost: " + currency_symbol + "%{y:,.0f}<extra></extra>"
					))

				fig.update_layout(
					title="Total Contract Cost by MRO Level and Maintenance Approach",
					xaxis_title="MRO Capability Level",
					yaxis_title=f"Total Contract Cost ({currency_symbol})",
					barmode="group",
					height=500,
					hovermode="x unified",
					template="plotly_white",
				)

				st.plotly_chart(fig, use_container_width=True, config=_plotly_export_config("MRO_Levels_Comparison"))

				# Cost per FH chart
				fig2 = go.Figure()
				for approach in mro_comparison_df_sorted["Maintenance Approach"].unique():
					approach_data = mro_comparison_df_sorted[mro_comparison_df_sorted["Maintenance Approach"] == approach]
					fig2.add_trace(go.Bar(
						x=approach_data["MRO Level"],
						y=approach_data["Cost/FH"] * display_factor,
						name=approach,
						text=[f"{currency_symbol}{v * display_factor:,.2f}" for v in approach_data["Cost/FH"]],
						textposition="outside",
						hovertemplate="<b>%{name}</b><br>MRO Level: %{x}<br>Cost/FH: " + currency_symbol + "%{y:,.2f}<extra></extra>"
					))

				fig2.update_layout(
					title="Cost per Flight Hour by MRO Level and Maintenance Approach",
					xaxis_title="MRO Capability Level",
					yaxis_title=f"Cost/FH ({currency_symbol})",
					barmode="group",
					height=500,
					hovermode="x unified",
					template="plotly_white",
				)

				st.plotly_chart(fig2, use_container_width=True, config=_plotly_export_config("MRO_Levels_Cost_FH"))

			except Exception as e:
				st.error(f"Error calculating MRO levels comparison: {str(e)}")

		elif tabs[i] == "Event Library":
			st.header("Event Library")
			scheduled_path = os.path.join("data", "scheduled_events.csv")
			unsched_path = os.path.join("data", "unscheduled_events.csv")
			default_scheduled_path = os.path.join("data", "scheduled_events_default.csv")
			default_unsched_path = os.path.join("data", "unscheduled_events_default.csv")
			backup_scheduled_original = os.path.join("backups", "No.1", "data", "scheduled_events.csv")
			backup_unsched_original = os.path.join("backups", "No.1", "data", "unscheduled_events.csv")

			if "scheduled_editor_version" not in st.session_state:
				st.session_state["scheduled_editor_version"] = 0
			if "unscheduled_editor_version" not in st.session_state:
				st.session_state["unscheduled_editor_version"] = 0
			if "scheduled_restore_info" not in st.session_state:
				st.session_state["scheduled_restore_info"] = "Not restored in this session"
			if "unscheduled_restore_info" not in st.session_state:
				st.session_state["unscheduled_restore_info"] = "Not restored in this session"

			# Bootstrap default snapshots from current library files once.
			if os.path.exists(scheduled_path) and not os.path.exists(default_scheduled_path):
				shutil.copy2(scheduled_path, default_scheduled_path)
			if os.path.exists(unsched_path) and not os.path.exists(default_unsched_path):
				shutil.copy2(unsched_path, default_unsched_path)

			st.caption("Edits saved here become the source data for all model calculations.")

			# Scheduled library editor
			st.subheader("Scheduled Events Library")
			st.caption(f"Last restore: {st.session_state['scheduled_restore_info']}")
			if os.path.exists(scheduled_path):
				scheduled_df = pd.read_csv(scheduled_path)
				scheduled_editor_key = f"scheduled_events_editor_{st.session_state['scheduled_editor_version']}"
				edited_scheduled_df = st.data_editor(
					scheduled_df,
					num_rows="dynamic",
					use_container_width=True,
					key=scheduled_editor_key,
				)
				col_s1, col_s2 = st.columns(2)
				with col_s1:
					if st.button("Save Scheduled Library", key="save_scheduled_library"):
						edited_scheduled_df.to_csv(scheduled_path, index=False)
						st.cache_data.clear()
						st.success("Scheduled events library saved.")
				with col_s2:
					if st.button("Restore Scheduled Default", key="restore_scheduled_default"):
						source_used = ""
						if os.path.exists(backup_scheduled_original):
							shutil.copy2(backup_scheduled_original, scheduled_path)
							source_used = "backups/No.1/data/scheduled_events.csv"
						elif os.path.exists(default_scheduled_path):
							shutil.copy2(default_scheduled_path, scheduled_path)
							source_used = "data/scheduled_events_default.csv"
						if os.path.exists(scheduled_path):
							# Force a fresh editor widget to avoid stale UI state.
							st.session_state["scheduled_editor_version"] += 1
							st.session_state["scheduled_restore_info"] = f"{source_used} at {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M:%S')}"
							st.cache_data.clear()
							st.success("Scheduled events library restored to default.")
							st.rerun()
						elif not os.path.exists(default_scheduled_path):
							st.warning("Scheduled default file not found.")
			else:
				st.warning("Scheduled events file not found.")

			# Unscheduled library editor
			st.subheader("Unscheduled Events Library")
			st.caption(f"Last restore: {st.session_state['unscheduled_restore_info']}")
			if os.path.exists(unsched_path):
				unsched_df = pd.read_csv(unsched_path)
				unscheduled_editor_key = f"unscheduled_events_editor_{st.session_state['unscheduled_editor_version']}"
				edited_unsched_df = st.data_editor(
					unsched_df,
					num_rows="dynamic",
					use_container_width=True,
					key=unscheduled_editor_key,
				)
				col_u1, col_u2 = st.columns(2)
				with col_u1:
					if st.button("Save Unscheduled Library", key="save_unscheduled_library"):
						edited_unsched_df.to_csv(unsched_path, index=False)
						st.cache_data.clear()
						st.success("Unscheduled events library saved.")
				with col_u2:
					if st.button("Restore Unscheduled Default", key="restore_unscheduled_default"):
						source_used = ""
						if os.path.exists(backup_unsched_original):
							shutil.copy2(backup_unsched_original, unsched_path)
							source_used = "backups/No.1/data/unscheduled_events.csv"
						elif os.path.exists(default_unsched_path):
							shutil.copy2(default_unsched_path, unsched_path)
							source_used = "data/unscheduled_events_default.csv"
						if os.path.exists(unsched_path):
							# Force a fresh editor widget to avoid stale UI state.
							st.session_state["unscheduled_editor_version"] += 1
							st.session_state["unscheduled_restore_info"] = f"{source_used} at {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M:%S')}"
							st.cache_data.clear()
							st.success("Unscheduled events library restored to default.")
							st.rerun()
						elif not os.path.exists(default_unsched_path):
							st.warning("Unscheduled default file not found.")
			else:
				st.warning("Unscheduled events file not found.")
		else:
			st.info(f"Content for {tabs[i]} goes here.")
