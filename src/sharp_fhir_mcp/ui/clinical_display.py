"""Clinical UI display builder — interactive HTML for MCP-UI clients.

Each ``build_*_section`` method takes a normalised summary dict (matching
the output of :mod:`sharp_fhir_mcp.fhir_utils`) and returns a self-contained
HTML fragment that the MCP-UI host can render in its sidebar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class ClinicalDisplayBuilder:
    """Build clinical-style HTML cards for MCP-UI."""

    COLORS: dict[str, str] = {
        "critical": "#dc2626",
        "warning": "#f59e0b",
        "info": "#3b82f6",
        "success": "#10b981",
        "muted": "#6b7280",
        "primary": "#2563eb",
        "background": "#f8fafc",
        "card": "#ffffff",
        "border": "#e2e8f0",
        "text": "#1e293b",
        "text_muted": "#64748b",
    }

    # ====
    # Top-level renderer
    # ====

    @classmethod
    def build_clinical_context_display(cls, context: dict[str, Any]) -> str:
        """Render a complete clinical-context HTML page from a context dict."""
        demographics = context.get("demographics") or {}
        patient_name = demographics.get("name") or "Unknown Patient"
        age = demographics.get("age")
        gender = demographics.get("gender") or ""

        html_parts = [
            cls._build_styles(),
            cls._build_header(patient_name, age, gender, context.get("patient_id")),
            cls._build_alerts_section(context.get("alerts") or []),
            cls._build_allergies_section(context.get("allergies") or []),
            cls._build_medications_section(context.get("active_medications") or []),
            cls._build_problems_section(context.get("active_problems") or []),
            cls._build_labs_section(context.get("recent_labs") or []),
            cls._build_visits_section(context.get("recent_encounters") or []),
            cls._build_immunizations_section(context.get("immunizations") or []),
            cls._build_demographics_section(demographics),
        ]

        if context.get("past_encounter_memories"):
            html_parts.append(
                cls._build_memories_section(context["past_encounter_memories"])
            )

        return f"""
        <div class="clinical-context">
            {''.join(html_parts)}
            <footer class="footer">
                Retrieved: {context.get('retrieved_at', datetime.now().isoformat())} ·
                SHARP-FHIR MCP
            </footer>
        </div>
        """

    # ====
    # CSS / header
    # ====

    @classmethod
    def _build_styles(cls) -> str:
        return f"""
        <style>
            .clinical-context {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: {cls.COLORS['background']};
                color: {cls.COLORS['text']};
                padding: 1rem;
                max-width: 1200px;
                margin: 0 auto;
            }}
            .header {{
                background: linear-gradient(135deg, {cls.COLORS['primary']} 0%, #1d4ed8 100%);
                color: white;
                padding: 1.5rem;
                border-radius: 12px;
                margin-bottom: 1rem;
            }}
            .header h1 {{ margin: 0 0 0.5rem 0; font-size: 1.75rem; }}
            .header .patient-meta {{ opacity: 0.9; font-size: 1rem; }}
            .card {{
                background: {cls.COLORS['card']};
                border: 1px solid {cls.COLORS['border']};
                border-radius: 8px;
                padding: 1rem;
                margin-bottom: 1rem;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }}
            .card-header {{
                display: flex;
                align-items: center;
                gap: 0.5rem;
                margin-bottom: 0.75rem;
                padding-bottom: 0.5rem;
                border-bottom: 1px solid {cls.COLORS['border']};
            }}
            .card-title {{ font-weight: 600; font-size: 1.1rem; margin: 0; }}
            .badge {{
                display: inline-block;
                padding: 0.25rem 0.5rem;
                border-radius: 9999px;
                font-size: 0.75rem;
                font-weight: 500;
            }}
            .badge-critical {{ background: #fef2f2; color: {cls.COLORS['critical']};
                border: 1px solid #fecaca; }}
            .badge-warning  {{ background: #fffbeb; color: {cls.COLORS['warning']};
                border: 1px solid #fde68a; }}
            .badge-info     {{ background: #eff6ff; color: {cls.COLORS['info']};
                border: 1px solid #bfdbfe; }}
            .alert {{
                padding: 1rem;
                border-radius: 8px;
                margin-bottom: 0.75rem;
                display: flex;
                align-items: flex-start;
                gap: 0.75rem;
            }}
            .alert-critical {{ background: #fef2f2; border-left: 4px solid {cls.COLORS['critical']}; }}
            .alert-warning  {{ background: #fffbeb; border-left: 4px solid {cls.COLORS['warning']}; }}
            .alert-info     {{ background: #eff6ff; border-left: 4px solid {cls.COLORS['info']}; }}
            .alert-icon {{ font-size: 1.25rem; }}
            .alert-content {{ flex: 1; }}
            .alert-title {{ font-weight: 600; margin: 0 0 0.25rem 0; }}
            .alert-details {{ font-size: 0.875rem; color: {cls.COLORS['text_muted']}; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
            th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid {cls.COLORS['border']}; }}
            th {{ font-weight: 600; color: {cls.COLORS['text_muted']};
                font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
            tr:last-child td {{ border-bottom: none; }}
            .abnormal {{ color: {cls.COLORS['critical']}; font-weight: 600; }}
            .empty-state {{ text-align: center; padding: 1rem;
                color: {cls.COLORS['text_muted']}; font-style: italic; }}
            .two-column {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 1rem;
            }}
            .detail-row {{
                display: flex;
                justify-content: space-between;
                padding: 0.5rem 0;
                border-bottom: 1px solid {cls.COLORS['border']};
            }}
            .detail-row:last-child {{ border-bottom: none; }}
            .detail-label {{ color: {cls.COLORS['text_muted']}; font-size: 0.875rem; }}
            .detail-value {{ font-weight: 500; }}
            .footer {{ text-align: center; padding: 1rem;
                color: {cls.COLORS['text_muted']}; font-size: 0.75rem; }}
            .section-icon {{ font-size: 1.25rem; }}
        </style>
        """

    @classmethod
    def _build_header(
        cls,
        name: str,
        age: int | None,
        gender: str,
        patient_id: Any,
    ) -> str:
        age_str = f"{age}yo" if age else ""
        gender_short = gender[0].upper() if gender else ""
        meta_parts = [p for p in (age_str, gender_short) if p]
        meta = " | ".join(meta_parts) if meta_parts else ""
        return f"""
        <div class="header">
            <h1>{cls._escape(name)}</h1>
            <div class="patient-meta">
                {meta}
                {f' | ID: {cls._escape(str(patient_id))}' if patient_id else ''}
            </div>
        </div>
        """

    # ====
    # Section builders (private classmethods)
    # ====

    @classmethod
    def _build_alerts_section(cls, alerts: list[dict[str, Any]]) -> str:
        if not alerts:
            return ""
        rendered: list[str] = []
        for alert in alerts:
            severity = alert.get("severity", "info")
            if severity == "high":
                severity = "critical"
            elif severity == "medium":
                severity = "warning"
            elif severity not in ("critical", "warning", "info"):
                severity = "info"
            icon = "⚠️" if severity == "critical" else ("⚡" if severity == "warning" else "ℹ️")

            details = alert.get("details") or []
            details_html = ""
            if details:
                items = "<br>• ".join(cls._escape(str(d)) for d in details[:5])
                details_html = f'<p class="alert-details">• {items}</p>'

            rendered.append(
                f"""
                <div class="alert alert-{severity}">
                    <span class="alert-icon">{icon}</span>
                    <div class="alert-content">
                        <p class="alert-title">{cls._escape(alert.get('message', ''))}</p>
                        {details_html}
                    </div>
                </div>
                """
            )
        return f"<div class='alerts-section'>{''.join(rendered)}</div>"

    @classmethod
    def _build_allergies_section(cls, allergies: list[dict[str, Any]]) -> str:
        if not allergies:
            content = '<p class="empty-state">No known allergies documented</p>'
        else:
            rows: list[str] = []
            for a in allergies:
                severity = (a.get("severity") or "").lower()
                severity_class = "abnormal" if severity in {"severe", "high"} else ""
                rows.append(
                    f"""
                    <tr>
                        <td class="{severity_class}">{cls._escape(a.get('allergen', ''))}</td>
                        <td>{cls._escape(a.get('reaction') or 'Not specified')}</td>
                        <td>{cls._escape(a.get('severity') or 'Unknown')}</td>
                    </tr>
                    """
                )
            content = f"""
                <table>
                    <thead><tr><th>Allergen</th><th>Reaction</th><th>Severity</th></tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            """
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">🚨</span>
                <h2 class="card-title">Allergies</h2>
                <span class="badge badge-critical">{len(allergies)} documented</span>
            </div>
            {content}
        </div>
        """

    @classmethod
    def _build_medications_section(cls, medications: list[dict[str, Any]]) -> str:
        if not medications:
            content = '<p class="empty-state">No active medications</p>'
        else:
            rows: list[str] = []
            for m in medications:
                rows.append(
                    f"""
                    <tr>
                        <td><strong>{cls._escape(m.get('name', ''))}</strong></td>
                        <td>{cls._escape(m.get('dose') or '')}</td>
                        <td>{cls._escape(m.get('frequency') or '')}</td>
                    </tr>
                    """
                )
            content = f"""
                <table>
                    <thead><tr><th>Medication</th><th>Dose</th><th>Frequency</th></tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            """
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">💊</span>
                <h2 class="card-title">Active Medications</h2>
                <span class="badge badge-info">{len(medications)} active</span>
            </div>
            {content}
        </div>
        """

    @classmethod
    def _build_problems_section(cls, problems: list[dict[str, Any]]) -> str:
        if not problems:
            content = '<p class="empty-state">No active problems documented</p>'
        else:
            rows: list[str] = []
            for p in problems:
                rows.append(
                    f"""
                    <tr>
                        <td>{cls._escape(p.get('name', ''))}</td>
                        <td><code>{cls._escape(p.get('icd_code') or 'N/A')}</code></td>
                        <td>{cls._escape(p.get('onset_date') or 'Unknown')}</td>
                    </tr>
                    """
                )
            content = f"""
                <table>
                    <thead><tr><th>Condition</th><th>ICD Code</th><th>Onset</th></tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            """
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">📋</span>
                <h2 class="card-title">Active Problems</h2>
                <span class="badge badge-warning">{len(problems)} active</span>
            </div>
            {content}
        </div>
        """

    @classmethod
    def _build_labs_section(cls, labs: list[dict[str, Any]]) -> str:
        if not labs:
            content = '<p class="empty-state">No recent lab results</p>'
        else:
            rows: list[str] = []
            for lab in labs:
                is_abnormal = bool(lab.get("abnormal"))
                abnormal_class = "abnormal" if is_abnormal else ""
                value = lab.get("value")
                unit = lab.get("unit") or ""
                value_display = f"{value if value is not None else ''} {unit}".strip()
                if is_abnormal:
                    value_display = f"⚠️ {value_display}"
                rows.append(
                    f"""
                    <tr>
                        <td>{cls._escape(lab.get('test', ''))}</td>
                        <td class="{abnormal_class}">{cls._escape(value_display)}</td>
                        <td>{cls._escape(lab.get('normal_range') or 'N/A')}</td>
                        <td>{cls._escape((lab.get('date') or '')[:10])}</td>
                    </tr>
                    """
                )
            content = f"""
                <table>
                    <thead>
                        <tr>
                            <th>Test</th>
                            <th>Value</th>
                            <th>Normal Range</th>
                            <th>Date</th>
                        </tr>
                    </thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            """
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">🔬</span>
                <h2 class="card-title">Recent Lab Results</h2>
            </div>
            {content}
        </div>
        """

    @classmethod
    def _build_visits_section(cls, visits: list[dict[str, Any]]) -> str:
        if not visits:
            return ""
        rows: list[str] = []
        for v in visits:
            date = v.get("date") or v.get("start") or ""
            rows.append(
                f"""
                <tr>
                    <td>{cls._escape(date[:10])}</td>
                    <td>{cls._escape(v.get('reason') or v.get('type') or 'Not specified')}</td>
                    <td>{cls._escape(v.get('status') or '')}</td>
                </tr>
                """
            )
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">📅</span>
                <h2 class="card-title">Recent Visits</h2>
            </div>
            <table>
                <thead><tr><th>Date</th><th>Reason</th><th>Status</th></tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """

    @classmethod
    def _build_immunizations_section(
        cls, immunizations: list[dict[str, Any]]
    ) -> str:
        if not immunizations:
            return ""
        rows: list[str] = []
        for v in immunizations:
            rows.append(
                f"""
                <tr>
                    <td>{cls._escape(v.get('vaccine', ''))}</td>
                    <td>{cls._escape(v.get('date_administered') or 'Unknown')}</td>
                    <td><code>{cls._escape(v.get('cvx_code') or 'N/A')}</code></td>
                </tr>
                """
            )
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">💉</span>
                <h2 class="card-title">Immunizations</h2>
                <span class="badge badge-info">{len(immunizations)} recorded</span>
            </div>
            <table>
                <thead><tr><th>Vaccine</th><th>Date</th><th>CVX Code</th></tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """

    @classmethod
    def _build_demographics_section(cls, demographics: dict[str, Any]) -> str:
        dob = cls._escape(demographics.get("date_of_birth") or "")
        gender = cls._escape(demographics.get("gender") or "")
        phone = cls._escape(demographics.get("phone") or "Not on file")
        email = cls._escape(demographics.get("email") or "Not on file")
        address = cls._escape(demographics.get("address") or "Not on file")
        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">👤</span>
                <h2 class="card-title">Demographics &amp; Contact</h2>
            </div>
            <div class="two-column">
                <div>
                    <div class="detail-row">
                        <span class="detail-label">Date of Birth</span>
                        <span class="detail-value">{dob}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Gender</span>
                        <span class="detail-value">{gender}</span>
                    </div>
                </div>
                <div>
                    <div class="detail-row">
                        <span class="detail-label">Phone</span>
                        <span class="detail-value">{phone}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Email</span>
                        <span class="detail-value">{email}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Address</span>
                        <span class="detail-value">{address}</span>
                    </div>
                </div>
            </div>
        </div>
        """

    @classmethod
    def _build_memories_section(cls, memories: Any) -> str:
        if isinstance(memories, dict) and "error" in memories:
            return ""
        if not memories:
            return ""

        items: list[str] = []
        if isinstance(memories, list):
            for mem in memories:
                if isinstance(mem, str):
                    items.append(f"<li>{cls._escape(mem[:300])}</li>")
                elif isinstance(mem, dict):
                    content = mem.get("content") or mem.get("text") or str(mem)
                    items.append(f"<li>{cls._escape(str(content)[:300])}</li>")
        if not items:
            return ""

        return f"""
        <div class="card">
            <div class="card-header">
                <span class="section-icon">🧠</span>
                <h2 class="card-title">Clinical Memory (Past Encounters)</h2>
            </div>
            <ul style="margin: 0; padding-left: 1.5rem;">
                {''.join(items[:5])}
            </ul>
        </div>
        """

    # ====
    # Public instance API (kept for backwards-compat with the old interface)
    # ====

    def build_alerts_section(self, alerts: list[dict[str, Any]]) -> str:
        return self._build_alerts_section(alerts)

    def build_allergies_section(self, allergies: list[dict[str, Any]]) -> str:
        return self._build_allergies_section(allergies)

    def build_medications_section(self, medications: list[dict[str, Any]]) -> str:
        return self._build_medications_section(medications)

    def build_problems_section(self, problems: list[dict[str, Any]]) -> str:
        return self._build_problems_section(problems)

    def build_labs_section(self, labs: list[dict[str, Any]]) -> str:
        return self._build_labs_section(labs)

    def build_visits_section(self, visits: list[dict[str, Any]]) -> str:
        return self._build_visits_section(visits)

    def build_immunizations_section(
        self, immunizations: list[dict[str, Any]]
    ) -> str:
        return self._build_immunizations_section(immunizations)

    def build_demographics_section(self, demographics: dict[str, Any]) -> str:
        return self._build_demographics_section(demographics)

    def build_memories_section(self, memories: Any) -> str:
        return self._build_memories_section(memories)

    # ====
    # Utilities
    # ====

    @staticmethod
    def _escape(text: Any) -> str:
        if text is None:
            return ""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )


__all__ = ["ClinicalDisplayBuilder"]
