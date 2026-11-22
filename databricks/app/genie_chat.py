"""'Ask the data' tab: a Genie chat over the gold tables.

Conversational data exploration of facilities and access. Not medical advice.
The tab only appears when the app has a Genie space attached (env GENIE_SPACE_ID,
set from the app resource 'genie-space' in app.yaml). Without it, the app is unchanged.

Auth: the app's service principal (same as the SQL connector). The space must grant the
service principal CAN RUN (added automatically when the space is attached as an app
resource), and the principal needs SELECT on the tables (it already has it on vf_gold/vf_silver).
"""
from __future__ import annotations

import os
import re

import pandas as pd
import streamlit as st

SPACE_ID = os.getenv("GENIE_SPACE_ID", "").strip()

CAVEAT = ("Answers come from the facility dataset. Facilities *state* their services; nothing here "
          "is verified, distances are straight-line, and the data covers about 10,000 facilities, "
          "not every facility in India.")

STARTERS = [
    "Which 10 districts have the largest population more than 60 km from a facility claiming emergency care?",
    "How many facilities in Bihar claim a blood bank, by district?",
    "Compare ICU claims per 100k people across states.",
    "Which states have the biggest shortfall of CHC specialists?",
]

# Questions that ask for diagnosis or treatment. Genie's own instructions also refuse these;
# this check stops them before they reach the model at all.
_MEDICAL = re.compile(
    r"\b(should i|what (should|do) i (take|do)|my (chest|head|stomach|child|baby|wife|husband|mother|father)"
    r"|i have|i('| a)m having|i feel|symptom|diagnos|dose|dosage|medicine for|treat(ment)? for|is it serious"
    r"|pain in|bleeding|fever|unconscious|not breathing)\b",
    re.I,
)

MEDICAL_REPLY = (
    "I can't help with symptoms, diagnosis or treatment. **In an emergency, call 108** "
    "(102 for pregnancy or a newborn). To find a facility for symptoms, use *Describe symptoms* on "
    "the Patients tab. Here you can ask about the data, for example how many facilities in a "
    "district claim a service."
)


def enabled() -> bool:
    return bool(SPACE_ID)


@st.cache_resource
def _client():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def _ask(question: str) -> dict:
    """Send one turn. Returns {'text', 'sql', 'description', 'table', 'suggested', 'error'}."""
    w = _client()
    conv_id = st.session_state.get("genie_conversation_id")
    if conv_id:
        msg = w.genie.create_message_and_wait(SPACE_ID, conv_id, question)
    else:
        msg = w.genie.start_conversation_and_wait(SPACE_ID, question)
        st.session_state["genie_conversation_id"] = msg.conversation_id
    out = {"text": [], "sql": None, "description": None, "table": None, "suggested": [], "error": None}
    if msg.error:
        out["error"] = str(getattr(msg.error, "error", msg.error))
    for att in msg.attachments or []:
        if att.text and att.text.content:
            out["text"].append(att.text.content)
        if att.query:
            out["sql"] = att.query.query
            out["description"] = att.query.description
            try:
                res = w.genie.get_message_attachment_query_result(
                    SPACE_ID, msg.conversation_id, msg.message_id, att.attachment_id)
                out["table"] = _to_frame(res.statement_response)
            except Exception as e:  # result may have expired or been too large
                out["error"] = f"Could not fetch the result table ({type(e).__name__})."
        if att.suggested_questions and att.suggested_questions.questions:
            out["suggested"] = list(att.suggested_questions.questions)[:3]
    return out


def _to_frame(resp) -> pd.DataFrame | None:
    if resp is None or resp.result is None:
        return None
    cols = [c.name for c in resp.manifest.schema.columns]
    df = pd.DataFrame(resp.result.data_array or [], columns=cols)
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().sum() == df[c].notna().sum():
            df[c] = conv
    return df


def _render(turn: dict, key: str):
    for t in turn.get("text", []):
        st.markdown(t)
    if turn.get("description"):
        st.markdown(turn["description"])
    if turn.get("table") is not None:
        df = turn["table"]
        st.dataframe(df, width="stretch", hide_index=True)
        if len(df) > 1 and df.shape[1] == 2 and pd.api.types.is_numeric_dtype(df.iloc[:, 1]):
            st.bar_chart(df.set_index(df.columns[0]).iloc[:, 0].head(25))
    if turn.get("sql"):
        with st.expander("SQL Genie ran"):
            st.code(turn["sql"], language="sql")
    if turn.get("error"):
        st.warning(turn["error"])
    if turn.get("table") is not None or turn.get("sql"):
        st.caption(CAVEAT)


def tab_genie():
    st.subheader("Ask the data")
    st.caption("Ask questions about facilities, services and access in plain language. "
               "This explores the dataset; it does not give medical advice. " + CAVEAT)
    if not enabled():
        st.info("Genie chat is not configured for this app yet.")
        return

    history = st.session_state.setdefault("genie_history", [])
    pending = st.session_state.pop("genie_pending", None)

    if not history:
        cols = st.columns(2)
        for i, q in enumerate(STARTERS):
            if cols[i % 2].button(q, key=f"genie_start_{i}", width="stretch"):
                pending = q

    for i, (q, turn) in enumerate(history):
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            _render(turn, f"h{i}")
    if history and history[-1][1].get("suggested"):
        cols = st.columns(len(history[-1][1]["suggested"]))
        for j, s in enumerate(history[-1][1]["suggested"]):
            if cols[j].button(s, key=f"genie_sugg_{len(history)}_{j}", width="stretch"):
                st.session_state["genie_pending"] = s
                st.rerun()

    typed = st.chat_input("Ask about facilities, districts or access...", key="genie_input")
    question = (typed or pending or "").strip()
    if not question:
        if history and st.button("New conversation", key="genie_reset"):
            st.session_state.pop("genie_conversation_id", None)
            st.session_state["genie_history"] = []
            st.rerun()
        return

    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        if _MEDICAL.search(question):
            turn = {"text": [MEDICAL_REPLY]}
        else:
            with st.spinner("Genie is writing and running a query..."):
                try:
                    turn = _ask(question)
                except Exception as e:
                    turn = {"error": f"Genie did not answer ({type(e).__name__}: {e})."}
        _render(turn, "new")
    history.append((question, turn))
    st.rerun()
