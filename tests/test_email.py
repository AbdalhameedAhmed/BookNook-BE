"""The auth emails render the Stitch HTML template safely and ship with a plain-text fallback."""

import re

from core.email import (
    CONFIRM_COPY,
    ICON_CID,
    RESET_COPY,
    build_message,
    render_email_html,
)

LINK = "https://rfoof.example/confirm-email#token=abc123"


def test_rendered_html_fills_every_slot_and_uses_the_real_link() -> None:
    for copy in (CONFIRM_COPY, RESET_COPY):
        rendered = render_email_html("Subject", copy, LINK)
        assert "{{" not in rendered and "}}" not in rendered
        assert rendered.count(f'href="{LINK}"') == 6  # buttons (incl. Outlook VML) and fallback links, en + ar
        assert copy.en_heading in rendered and copy.ar_heading in rendered
        assert f"cid:{ICON_CID}" in rendered
        assert "fayoum_act_" not in rendered and "fayoum_rst_" not in rendered  # no Stitch sample tokens


def test_slot_values_are_html_escaped() -> None:
    rendered = render_email_html("<b>x</b>", CONFIRM_COPY, 'https://x.test/?a=1&b="2"')
    assert "<b>x</b>" not in rendered
    assert "&lt;b&gt;x&lt;/b&gt;" in rendered
    assert 'href="https://x.test/?a=1&amp;b=&quot;2&quot;"' in rendered


def test_reset_email_uses_the_ochre_warning_box() -> None:
    assert 'class="ochre-box"' in render_email_html("s", RESET_COPY, LINK)
    assert 'class="ochre-box"' not in render_email_html("s", CONFIRM_COPY, LINK)


def test_message_has_text_html_and_inline_icon() -> None:
    message = build_message("reader@example.com", "Subject", f"Plain body {LINK}", CONFIRM_COPY, LINK, "Rfoof <no-reply@test>")
    assert message.get_content_type() == "multipart/alternative"
    parts = {part.get_content_type(): part for part in message.walk()}
    assert LINK in parts["text/plain"].get_content()
    assert "Welcome to Rfoof" in parts["text/html"].get_content()
    icon = parts["image/png"]
    assert icon["Content-ID"] == f"<{ICON_CID}>"
    assert icon.get_content().startswith(b"\x89PNG")
    assert re.search(r"multipart/related", message.as_string())
