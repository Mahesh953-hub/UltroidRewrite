class ButtonKit:
    """The Telegram button toolkit: inline builders, styles, and icons.

    One instance (``button_kit``) owns the custom button-type registry;
    the module-level functions below are backward-compatible aliases.
    """

    def __init__(self):
        self._registry = {}

    def _is_built_button(self, obj) -> bool:
        return isinstance(obj, (Button, TLObject))

    def _normalize_style(self, style):
        """Normalise a button style to a value ``Button.inline`` accepts."""
        if style is None:
            return None
        if isinstance(style, str):
            return _STYLE_MAP.get(style.lower())
        if isinstance(style, int) and not isinstance(style, bool):
            return _STYLE_MAP.get(style)
        return None

    def _normalize_icon(self, icon):
        """Normalise a button icon to a custom-emoji document id (``int``).

        The fork's ``KeyboardButtonStyle.icon`` field is a ``long`` holding the
        document id of the custom emoji shown inside the button. Anything that
        is not an int (or a digit string) becomes ``None`` so a bad icon never
        breaks TL serialisation at send time.
        """
        if icon is None or isinstance(icon, bool):
            return None
        if isinstance(icon, int):
            return icon
        if isinstance(icon, str) and icon.isdigit():
            return int(icon)
        if isinstance(icon, str):
            _icon = document_id_for(icon)
            return _icon if _icon else None
        return None

    def _fold_stylized(self, text: str) -> str:
        """Fold "stylized" small-caps / phonetic-extension Unicode letters (the
        section-header convention this codebase uses, e.g. ``"Aᴅᴅᴏɴs"``,
        ``"« Bᴀᴄᴋ"``) down to plain ASCII, purely so the keyword matcher below
        can still recognise them.

        These glyphs have no case-folding or decomposition mapping in Unicode -
        they're distinct IPA/phonetic-extension code points, not accented Latin
        letters - so ``str.lower()``/``unicodedata.normalize`` leave them alone.
        Their *character name* does encode the plain letter though
        (``U+1D05 LATIN LETTER SMALL CAPITAL D``), so that's what this walks.
        """
        out = []
        for ch in text:
            if ch.isascii():
                out.append(ch)
                continue
            try:
                name = unicodedata.name(ch)
            except ValueError:
                out.append(ch)
                continue
            if name.startswith("LATIN LETTER SMALL CAPITAL "):
                out.append(name.rsplit(" ", 1)[-1].lower())
            else:
                out.append(ch)
        return "".join(out)

    def _cbs_words(self, data):
        """Yield lowercase words found in *data*, segment by segment, in order.

        ``data`` is typically structured colon-model callback data
        (``"assistant:games:aki"`` -> ``"assistant"``, ``"games"``, ``"aki"``),
        but this also degrades gracefully for a plain (non-colon) callback
        string, a URL, or a switch-inline query - ``str.split(":")`` on a
        single-segment string is just ``[string]``, and stray punctuation inside
        a segment is stripped by the word regex.
        """
        if isinstance(data, bytes):
            data = data.decode("utf-8", "ignore")
        if data is None or isinstance(data, (int, float)):
            return
        for segment in str(data).lower().split(":"):
            yield from _WORD_RE.findall(segment)

    def _default_icon_name(self, text, data=None, type_hint=None, fallback=None):
        """Resolve a default icon *name* (a key of ``DEFAULT_ICON_UNICODE``) or ``None``.

        Precedence: callback-data (cbs) words first, in segment order - this is
        the primary source, since callback data is structured and stable while
        titles are free-form display text. Title words (folded to ASCII first,
        see :func:`_fold_stylized`) are a secondary source, checked only if no
        cbs word matched. A per-button-kind default (``type_hint``) is checked
        last, only for the handful of structurally-reliable kinds in
        :data:`_TYPE_ICON_NAMES`. If nothing matches at all, ``fallback`` (``None``
        by default) is returned - i.e. no icon, not a generic one.
        """
        for word in self._cbs_words(data):
            name = _WORD_ICON_NAMES.get(word)
            if name:
                return name
        for word in _WORD_RE.findall(self._fold_stylized(str(text or "")).lower()):
            name = _WORD_ICON_NAMES.get(word)
            if name:
                return name
        return _TYPE_ICON_NAMES.get(type_hint, fallback)

    def _has_icon_glyph(self, text: str) -> bool:
        """True if *text* already starts or ends with an emoji-like glyph
        (crude, avoids double-iconifying a caption such as ``"Close ❌"``).

        The ``0x2190`` threshold (arrows block onward) deliberately sits above
        the stylized small-caps letters (``U+1D00``-``U+1D7F``) used across the
        codebase's captions, so those never count as icons.
        """
        stripped = text.strip() if text else ""
        if not stripped:
            return False
        return ord(stripped[0]) >= 0x2190 or ord(stripped[-1]) >= 0x2190

    def _resolve_button_icon(
        self, text, icon_elem, *, auto, data=None, type_hint=None, fallback=None
    ):
        """Resolve the final ``(text, style_icon_document_id)`` for one button.

        ``icon_elem`` is the button's own icon slot (an explicit id/digit-string
        or an icon-table name like ``"back"`` always wins over auto-selection;
        ``False`` force-disables auto-selection
        for just this button, overriding a truthy ``auto``). ``auto`` is the
        ``ikm(..., icon=True)`` / ``rkm(..., icon=True)`` call-level toggle.

        Matching precedence: explicit per-button ``icon_elem`` > cbs (callback
        data) keyword > title keyword > per-kind ``type_hint`` icon > no icon.
        See :func:`_default_icon_name`.
        """
        if icon_elem is False:
            return text, None
        # An icon-table *name* ("back", "close", …) is an explicit per-button
        # override too: prefix its emoji and use its document id directly.
        if (
            isinstance(icon_elem, str)
            and not icon_elem.isdigit()
            and icon_elem.lower() in DEFAULT_ICON_UNICODE
        ):
            name = icon_elem.lower()
        else:
            doc_id = self._normalize_icon(icon_elem)
            if doc_id is not None:
                return text, doc_id
            if not auto:
                return text, None
            name = self._default_icon_name(
                text, data=data, type_hint=type_hint, fallback=fallback
            )
            if name is None:
                return text, None
        emoji = DEFAULT_ICON_UNICODE.get(name)
        if emoji and not self._has_icon_glyph(text):
            text = f"{emoji} {text}"
        return text, TG_ANDROID_ICON_DOCUMENT_IDS.get(name)

    def register_button_type(self, name, factory=None):
        """Register a custom ``ikm``/``rkm`` tuple-form button kind.

        ``name`` is the string used as a tuple's 2nd element, e.g.
        ``("Text", name, ...)``. ``factory(text, val, btn_tuple)`` is called
        once per matching tuple and must return a built Telethon button (a
        ``Button`` helper instance or a raw TL ``KeyboardButton*`` object).
        ``val`` is the tuple's 3rd element (``None`` if absent); ``btn_tuple``
        is the full tuple, for factories that need later elements.

        Can be used directly or as a decorator::

            register_button_type("copy", my_factory)

            @register_button_type("copy")
            def my_factory(self, text, val, btn_tuple):
                ...

        Registering an already-used name replaces the previous factory. The
        built-in kinds (``"url"``, ``"user"``, ``"web_app"``, ``"switch_inline"``,
        ``"request_user"``/``"request_chat"``/``"request_broadcast"``, ``"auth"``,
        ``"buy"``, ``"game"``) are handled natively and are never looked up here.
        """
        key = str(name).lower()
        if factory is None:

            def _decorator(self, fn):
                self._registry[key] = fn
                return fn

            return _decorator
        self._registry[key] = factory
        return factory

    def _build_copy_button(self, text, val, btn_tuple):
        """Built-in ``"copy"`` registration: a copy-to-clipboard button.

        ``("Text", "copy", copy_text[, style[, icon]])``. Uses the native
        ``KeyboardButtonCopy`` TL type when this Telethon build ships it,
        otherwise degrades to an inline callback carrying the copy text.
        """
        copy_text = str(val) if val is not None else text
        style = btn_tuple[3] if len(btn_tuple) > 3 else None
        icon = btn_tuple[4] if len(btn_tuple) > 4 else None
        style, icon = self._normalize_style(style), self._normalize_icon(icon)
        cls = getattr(_types, "KeyboardButtonCopy", None)
        if cls is None:
            return Button.inline(
                text, data=copy_text.encode()[:64], style=style, icon=icon
            )
        return cls(text=text, copy_text=copy_text, style=Button._get_style(style, icon))

    def _build_toggle_button(self, text, val, btn_tuple):
        """Built-in ``"toggle"`` registration: an on/off inline callback button.

        ``("Text", "toggle", callback_data[, is_on[, style]])`` - prefixes the
        title with a checked/unchecked box depending on ``is_on``.
        """
        is_on = bool(btn_tuple[3]) if len(btn_tuple) > 3 else False
        style = self._normalize_style(btn_tuple[4] if len(btn_tuple) > 4 else None)
        box = "☑️" if is_on else "⬜"
        return Button.inline(f"{box} {text}", data=val, style=style)

    def request_peer_button(
        self,
        text,
        peer="user",
        *,
        button_id=1,
        max_quantity=1,
        style=None,
        icon=None,
        **peer_kwargs,
    ):
        """Build a raw ``KeyboardButtonRequestPeer`` TL button.

        ``peer`` is one of ``"user"`` / ``"chat"`` / ``"broadcast"`` (the
        ``"request_"``-prefixed ikm type names are also accepted). Extra keyword
        arguments are forwarded to the ``RequestPeerType*`` constructor, e.g.
        ``request_peer_button("Bots", "user", bot=True)`` or
        ``request_peer_button("Forums", "chat", forum=True)``.

        .. warning::
            Telegram only allows request-peer buttons in *reply keyboards*
            (they are not inline buttons - the fork's ``Button._is_inline`` does
            not list them). Sending one inside an inline markup will be rejected
            by Telegram with ``BUTTON_TYPE_INVALID``.

        Raises ``ValueError`` for an unknown peer kind and ``NotImplementedError``
        when the installed Telethon build lacks the request-peer TL types.
        """
        kind = str(peer).lower()
        if kind.startswith("request_"):
            kind = kind[len("request_") :]
        try:
            cls_name = _REQUEST_PEER_TYPES[kind]
        except KeyError:
            raise ValueError(
                f"Unknown request-peer type {peer!r}; "
                f"expected one of {sorted(_REQUEST_PEER_TYPES)}"
            ) from None
        peer_cls = getattr(_types, cls_name, None)
        button_cls = getattr(_types, "KeyboardButtonRequestPeer", None)
        if peer_cls is None or button_cls is None:
            raise NotImplementedError(
                "This Telethon build does not provide the KeyboardButtonRequestPeer TL types"
            )
        style = self._normalize_style(style)
        icon = self._normalize_icon(icon)
        style_obj = (
            Button._get_style(style, icon)
            if (style is not None or icon is not None)
            else None
        )
        return button_cls(
            text=text,
            button_id=button_id,
            peer_type=peer_cls(**peer_kwargs),
            max_quantity=max_quantity,
            style=style_obj,
        )

    def web_app_button(self, text, url, *, style=None, icon=None, simple=False):
        """Build a web_app keyboard button with graceful degradation.

        By default builds a ``KeyboardButtonWebView`` (inline-keyboard web_app
        button that can send data back to the bot). Pass ``simple=True`` for a
        reply-keyboard ``KeyboardButtonSimpleWebView`` (opens the web app without
        the data round-trip).

        On Telethon builds that lack the raw web_app button types, this falls back
        to a ``Button.url`` button (so the link is still usable) or, if ``url`` is
        empty, to an inline callback button carrying the literal ``"web_app"``.
        """
        style = self._normalize_style(style)
        icon = self._normalize_icon(icon)
        style_obj = (
            Button._get_style(style, icon)
            if (style is not None or icon is not None)
            else None
        )

        cls = _SimpleWebViewButtonCls if simple else _WebViewButtonCls
        if cls is not None:
            return cls(text=text, url=url or "", style=style_obj)

        if url:
            return Button.url(text, url=url, style=style, icon=icon)
        return Button.inline(text, data=b"web_app", style=style, icon=icon)

    def send_web_view_result_message(self, bot_query_id: str, result):
        """Build a ``SendWebViewResultMessageRequest`` TL request when available.

        ``result`` is an ``InputBotInlineResult`` object. Raises
        ``NotImplementedError`` on Telethon builds that lack this request.
        """
        cls = _SendWebViewResultMsgCls
        if cls is None:
            raise NotImplementedError(
                "This Telethon build does not provide SendWebViewResultMessageRequest"
            )
        return cls(bot_query_id=bot_query_id, result=result)

    def _web_app_button_from_tuple(self, text, btn_tuple, val, *, icon=None):
        """Shared parser for the ikm/rkm ``('text', 'web_app', url, ...)`` tuple forms.

        ``btn_tuple`` is the full tuple, ``val`` the URL in its 3rd element,
        ``icon`` the already-resolved style-icon document id (or ``None``).
        """
        simple = btn_tuple[1] in ("simple_web_app", "simple_webapp")
        style = btn_tuple[3] if len(btn_tuple) > 3 else None
        return self.web_app_button(
            text, val or "", style=style, icon=icon, simple=simple
        )

    def ids(self, inline_message_id: str) -> tuple:
        """
        Resolves an inline_message_id into (chat_id, message_id).
        Handles both normal and bot-API style packed IDs.
        """
        data = resolve_inline_message_id(inline_message_id)
        try:
            cid, mid = data.peer, data.id
        except AttributeError:
            cid, mid = struct.unpack(">ii", data.id.to_bytes(8, signed=True))

        if cid < 0 or cid >= MAX_CHANNEL_ID:
            cid = get_peer_id(abs(cid))

        return cid, mid

    def ikm(self, data: list, *, icon: bool = False):
        """
        Constructs an inline keyboard as a list-of-lists of Button objects.

        Telethon's send_message / reply / edit_message `buttons=` param accepts
        list-of-lists and serialises them internally. Do NOT return a pre-built
        ReplyInlineMarkup - that causes ReplyMarkupInvalidError.

        Pre-built Telethon button objects (``Button`` helper instances and raw
        ``KeyboardButton*`` TL objects) pass through untouched - nested inside a
        row, as a bare row, or as the whole input - and are detected positively
        before any tuple-spec len()/indexing happens.

        Supported tuple formats per button:
          (text, cb)                              → inline button
          (text, cb, style)                       → inline button with colour style
          (text, cb, style, icon)                 → inline button, colour + icon
          (text, "url", url)                      → URL button
          (text, "url", url, style[, icon])       → URL button with colour style
          (text, "switch_inline", query[, same_peer[, style[, icon]]])
                                                  → inline switch-to-inline-query button
          (text, "user", uid)                     → request-peer button when the
                                                    Telethon build supports it,
                                                    otherwise an inline fallback
          (text, "request_user"|"request_chat"|"request_broadcast", button_id[, max_quantity[, style[, icon]]])
                                                  → raw KeyboardButtonRequestPeer
                                                    (reply-keyboard contexts only)
          (text, "web_app", url)                  → inline web_app button
                                                    (KeyboardButtonWebView; falls
                                                    back to a URL button on older
                                                    Telethon builds)
          (text, "simple_webapp", url)            → reply-keyboard simple web_app
                                                    button (KeyboardButtonSimpleWebView)
          (text, name, ...)                       → any kind registered via
                                                    :func:`register_button_type`

        ``style`` may be one of the strings "default"/"primary"/"success"/"danger"
        or the matching Telegram Bot API integers 0/1/2/3. ``icon`` is a
        custom-emoji document id (int) or an icon-table name (``"back"``,
        ``"close"``, …) - an explicit value here always wins,
        ``False`` disables auto-selection for that one button. Both apply to
        inline AND url buttons (the fork styles both); unknown values degrade to
        the default look instead of raising.

        ``icon=True`` (default ``False``) turns on automatic icon selection for
        every button in this call that doesn't already have an explicit icon:
        a matching unicode emoji (see :data:`DEFAULT_ICON_UNICODE`) is prefixed
        onto the button title, and the matching :data:`TG_ANDROID_ICON_DOCUMENT_IDS`
        entry (if filled in) is used as the style icon. Matching precedence is
        per-button ``icon=`` element > callback-data keyword > title keyword >
        button-type keyword > no icon - there is no generic fallback icon, so
        turning this on is safe to do call-wide without spamming every unrelated
        button. See :func:`_default_icon_name` for the exact matching rules.

        The pinned fork has no ``Button.request_peer`` helper, so
        ``("…", "user", uid)`` deliberately produces an inline callback button
        whose data is the user id. The capability is probed once at import time
        (``_HAS_REQUEST_PEER``), so if the fork ever grows the helper the native
        button is used automatically. ``("…", "web_app", url)`` similarly probes
        ``_HAS_WEBVIEW_BUTTONS`` once at import.

        Usage:
          ikm([("Text", "cb")])
          ikm([[("A", "cb1"), ("B", "cb2")], [("C", "url", "https://...")]])
          ikm([("Confirm", "yes", "success"), ("Cancel", "no", "danger")])
          ikm([("Pick user", "request_user", 1)])
          ikm([("Open app", "web_app", "https://example.com/app")])
          ikm([("Back", "back")], icon=True)  # auto-prefixes "◀️ Back"
        """
        auto_icon = icon

        # a bare top-level tuple or pre-built button is a single one-button row
        if isinstance(data, tuple) or self._is_built_button(data):
            data = [data]

        rows = []
        for row in data:
            current_row = []
            if isinstance(row, tuple) or self._is_built_button(row):
                row = [row]

            for btn in row:
                if self._is_built_button(btn):
                    current_row.append(btn)
                    continue

                if not isinstance(btn, (list, tuple)):
                    current_row.append(btn)
                    continue

                if len(btn) < 2:
                    continue

                text = btn[0]
                bt_type = btn[1]
                val = btn[2] if len(btn) > 2 else None

                if bt_type == "url":
                    # (text, "url", url[, style[, icon]])
                    style = btn[3] if len(btn) > 3 else None
                    icon_elem = btn[4] if len(btn) > 4 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, data=val, type_hint="url"
                    )
                    current_row.append(
                        Button.url(
                            text,
                            url=val or "",
                            style=self._normalize_style(style),
                            icon=icon_val,
                        )
                    )
                elif bt_type == "switch_inline":
                    # (text, "switch_inline", query[, same_peer[, style[, icon]]])
                    same_peer = bool(btn[3]) if len(btn) > 3 else False
                    style = btn[4] if len(btn) > 4 else None
                    icon_elem = btn[5] if len(btn) > 5 else None
                    text, icon_val = self._resolve_button_icon(
                        text,
                        icon_elem,
                        auto=auto_icon,
                        data=val,
                        type_hint="switch_inline",
                    )
                    current_row.append(
                        Button.switch_inline(
                            text,
                            query=val or "",
                            same_peer=same_peer,
                            style=self._normalize_style(style),
                            icon=icon_val,
                        )
                    )
                elif bt_type == "user":
                    text, _icon_val = self._resolve_button_icon(
                        text, None, auto=auto_icon, data=val, type_hint="user"
                    )
                    if _HAS_REQUEST_PEER:
                        current_row.append(
                            Button.request_peer(
                                text, button_id=val if isinstance(val, int) else 1
                            )
                        )
                    else:
                        current_row.append(
                            Button.inline(text, data=str(val or "user").encode()[:64])
                        )
                elif bt_type in ("request_user", "request_chat", "request_broadcast"):
                    # (text, "request_*", button_id[, max_quantity[, style[, icon]]])
                    icon_elem = btn[5] if len(btn) > 5 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint=bt_type
                    )
                    current_row.append(
                        self.request_peer_button(
                            text,
                            bt_type,
                            button_id=val if isinstance(val, int) else 1,
                            max_quantity=(
                                btn[3]
                                if len(btn) > 3 and isinstance(btn[3], int)
                                else 1
                            ),
                            style=btn[4] if len(btn) > 4 else None,
                            icon=icon_val,
                        )
                    )
                elif bt_type in (
                    "web_app",
                    "webapp",
                    "simple_web_app",
                    "simple_webapp",
                ):
                    icon_elem = btn[4] if len(btn) > 4 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, data=val, type_hint="web_app"
                    )
                    current_row.append(
                        self._web_app_button_from_tuple(text, btn, val, icon=icon_val)
                    )
                elif bt_type in ("auth", "buy", "game"):
                    icon_elem = btn[3] if len(btn) > 3 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint=bt_type
                    )
                    current_row.append(
                        Button.inline(
                            text,
                            data=(
                                (val or bt_type).encode()[:64]
                                if isinstance(val, str)
                                else val or bt_type.encode()
                            ),
                            icon=icon_val,
                        )
                    )
                else:
                    factory = (
                        self._registry.get(str(bt_type).lower())
                        if isinstance(bt_type, str)
                        else None
                    )
                    if factory is not None:
                        current_row.append(factory(text, val, btn))
                        continue
                    # (text, callback_data)  or  (text, callback_data, style[, icon])
                    # ``val`` is the 3rd element, i.e. the style for inline buttons.
                    icon_elem = btn[3] if len(btn) > 3 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, data=bt_type, type_hint=None
                    )
                    current_row.append(
                        Button.inline(
                            text,
                            data=bt_type,
                            style=self._normalize_style(val),
                            icon=icon_val,
                        )
                    )

            if current_row:
                rows.append(current_row)

        return rows or None

    def rkm(
        self,
        data: list,
        *,
        icon: bool = False,
        resize: bool = None,
        single_use: bool = None,
        selective: bool = None,
        placeholder: str = None,
    ):
        """
        Constructs a reply keyboard (NOT inline) as a list-of-lists of Button objects.

        Sibling of :func:`ikm` for the button kinds that only exist in *reply*
        keyboards (plain text, request_location/phone/poll/peer, the "simple"
        web_app view). Same passthrough rules for pre-built buttons, same
        style/icon/registry handling, same tolerant defaults.

        Supported tuple formats per button:
          "Text"                                   → plain text button
          (text,)  or  (text, "text")               → plain text button
          (text, "text", style[, icon])             → plain text button, styled
          (text, "location")                        → request_location button
          (text, "phone")                           → request_phone button
          (text, "poll"[, quiz[, style[, icon]]])    → request_poll button
          (text, "user"|"request_user"|"chat"|"request_chat"|"broadcast"|"request_broadcast",
           button_id[, max_quantity[, style[, icon]]])
                                                     → raw KeyboardButtonRequestPeer
          (text, "web_app"|"simple_webapp", url)     → KeyboardButtonSimpleWebView
                                                      (the only web_app kind valid
                                                      inside a reply keyboard)
          (text, name, ...)                         → any kind registered via
                                                      :func:`register_button_type`
                                                      (shared registry with ``ikm``)

        ``icon=True`` enables the same default-icon auto-selection as ``ikm``
        (see :data:`DEFAULT_ICON_UNICODE`); an unrecognised tuple type falls
        back to a plain text button instead of raising.

        ``resize``/``single_use``/``selective``/``placeholder`` configure the
        resulting ``ReplyKeyboardMarkup`` the same way ``Button.text(...)``'s
        keyword arguments do - set them here once instead of on every button.
        They are honoured even when every button in the keyboard is a raw TL
        object (``request_peer_button()``, a ``simple_webapp`` button, ...)
        rather than a ``Button`` wrapper - this function wraps whatever it
        built so the options always survive serialisation.

        Usage:
          rkm(["Yes", "No"])
          rkm([("Share number", "phone")], resize=True)
          rkm([("Pick a user", "request_user", 1)], single_use=True)
        """
        auto_icon = icon

        if isinstance(data, (str, tuple)) or self._is_built_button(data):
            data = [data]

        rows = []
        for row in data:
            current_row = []

            if isinstance(row, (str, tuple)) or self._is_built_button(row):
                row = [row]

            for btn in row:
                if self._is_built_button(btn):
                    current_row.append(btn)
                    continue

                if isinstance(btn, str):
                    text, bt_type, val, btn = btn, "text", None, (btn, "text")
                elif isinstance(btn, (list, tuple)):
                    if len(btn) < 1:
                        continue
                    text = btn[0]
                    bt_type = btn[1] if len(btn) > 1 else "text"
                    val = btn[2] if len(btn) > 2 else None
                else:
                    current_row.append(btn)
                    continue

                if bt_type == "text":
                    # (text, "text", style[, icon]) - no ``val`` slot of its own.
                    style = btn[2] if len(btn) > 2 else None
                    icon_elem = btn[3] if len(btn) > 3 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint="text", fallback=None
                    )
                    current_row.append(
                        Button.text(
                            text, style=self._normalize_style(style), icon=icon_val
                        )
                    )
                elif bt_type in ("location", "request_location"):
                    style = btn[3] if len(btn) > 3 else None
                    icon_elem = btn[4] if len(btn) > 4 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint="location"
                    )
                    current_row.append(
                        Button.request_location(
                            text, style=self._normalize_style(style), icon=icon_val
                        )
                    )
                elif bt_type in ("phone", "request_phone"):
                    style = btn[3] if len(btn) > 3 else None
                    icon_elem = btn[4] if len(btn) > 4 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint="phone"
                    )
                    current_row.append(
                        Button.request_phone(
                            text, style=self._normalize_style(style), icon=icon_val
                        )
                    )
                elif bt_type in ("poll", "request_poll"):
                    quiz = bool(val) if len(btn) > 2 else False
                    style = btn[3] if len(btn) > 3 else None
                    icon_elem = btn[4] if len(btn) > 4 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint="poll"
                    )
                    current_row.append(
                        Button.request_poll(
                            text,
                            style=self._normalize_style(style),
                            icon=icon_val,
                            force_quiz=quiz,
                        )
                    )
                elif bt_type in (
                    "user",
                    "request_user",
                    "chat",
                    "request_chat",
                    "broadcast",
                    "request_broadcast",
                ):
                    icon_elem = btn[5] if len(btn) > 5 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint=bt_type
                    )
                    current_row.append(
                        self.request_peer_button(
                            text,
                            bt_type,
                            button_id=val if isinstance(val, int) else 1,
                            max_quantity=(
                                btn[3]
                                if len(btn) > 3 and isinstance(btn[3], int)
                                else 1
                            ),
                            style=btn[4] if len(btn) > 4 else None,
                            icon=icon_val,
                        )
                    )
                elif bt_type in (
                    "web_app",
                    "webapp",
                    "simple_web_app",
                    "simple_webapp",
                ):
                    # Only KeyboardButtonSimpleWebView is valid in reply keyboards.
                    icon_elem = btn[4] if len(btn) > 4 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, data=val, type_hint="web_app"
                    )
                    current_row.append(
                        web_app_button(
                            text,
                            val or "",
                            style=btn[3] if len(btn) > 3 else None,
                            icon=icon_val,
                            simple=True,
                        )
                    )
                else:
                    factory = (
                        self._registry.get(str(bt_type).lower())
                        if isinstance(bt_type, str)
                        else None
                    )
                    if factory is not None:
                        current_row.append(factory(text, val, btn))
                        continue
                    # Unrecognised kind: degrade to a plain text button rather
                    # than raising, matching ikm's tolerant style.
                    icon_elem = btn[3] if len(btn) > 3 else None
                    text, icon_val = self._resolve_button_icon(
                        text, icon_elem, auto=auto_icon, type_hint="text", fallback=None
                    )
                    current_row.append(Button.text(text, icon=icon_val))

            if current_row:
                rows.append(current_row)

        if rows and any(
            v is not None for v in (resize, single_use, selective, placeholder)
        ):
            for row in rows:
                for i, btn in enumerate(row):
                    if isinstance(btn, Button):
                        raw = btn.button
                        prev_resize, prev_single_use = btn.resize, btn.single_use
                        prev_selective, prev_placeholder = (
                            btn.selective,
                            btn.placeholder,
                        )
                        persistent = btn.persistent
                    elif isinstance(btn, TLObject):
                        raw = btn
                        prev_resize = prev_single_use = prev_selective = (
                            prev_placeholder
                        ) = None
                        persistent = None
                    else:
                        continue
                    row[i] = Button(
                        raw,
                        resize=resize if resize is not None else prev_resize,
                        single_use=(
                            single_use if single_use is not None else prev_single_use
                        ),
                        selective=(
                            selective if selective is not None else prev_selective
                        ),
                        persistent=persistent,
                        placeholder=(
                            placeholder if placeholder is not None else prev_placeholder
                        ),
                    )

        return rows or None


button_kit = ButtonKit()


def _is_built_button(*args, **kwargs):
    return button_kit._is_built_button(*args, **kwargs)


def _normalize_style(*args, **kwargs):
    return button_kit._normalize_style(*args, **kwargs)


def _normalize_icon(*args, **kwargs):
    return button_kit._normalize_icon(*args, **kwargs)


def _fold_stylized(*args, **kwargs):
    return button_kit._fold_stylized(*args, **kwargs)


def _cbs_words(*args, **kwargs):
    return button_kit._cbs_words(*args, **kwargs)


def _default_icon_name(*args, **kwargs):
    return button_kit._default_icon_name(*args, **kwargs)


def _has_icon_glyph(*args, **kwargs):
    return button_kit._has_icon_glyph(*args, **kwargs)


def _resolve_button_icon(*args, **kwargs):
    return button_kit._resolve_button_icon(*args, **kwargs)


def register_button_type(*args, **kwargs):
    return button_kit.register_button_type(*args, **kwargs)


def _build_copy_button(*args, **kwargs):
    return button_kit._build_copy_button(*args, **kwargs)


def _build_toggle_button(*args, **kwargs):
    return button_kit._build_toggle_button(*args, **kwargs)


def request_peer_button(*args, **kwargs):
    return button_kit.request_peer_button(*args, **kwargs)


def web_app_button(*args, **kwargs):
    return button_kit.web_app_button(*args, **kwargs)


def send_web_view_result_message(*args, **kwargs):
    return button_kit.send_web_view_result_message(*args, **kwargs)


def _web_app_button_from_tuple(*args, **kwargs):
    return button_kit._web_app_button_from_tuple(*args, **kwargs)


def ids(*args, **kwargs):
    return button_kit.ids(*args, **kwargs)


def ikm(*args, **kwargs):
    return button_kit.ikm(*args, **kwargs)


def rkm(*args, **kwargs):
    return button_kit.rkm(*args, **kwargs)


import logging
import re
import struct
import unicodedata

from telethon import Button
from telethon.tl import TLObject, functions as _functions, types as _types
from telethon.utils import resolve_inline_message_id, get_peer_id

from .icons import document_id_for

_LOGS = logging.getLogger(__name__)

MAX_CHANNEL_ID = -1000000000000


_STYLE_MAP = {
    # normal
    "default": None,
    "primary": "primary",
    "success": "success",
    "danger": "danger",
    # index
    0: None,
    1: "primary",
    2: "success",
    3: "danger",
    # by name
    "red": "danger",
    "green": "success",
    "blue": "primary",
}


DEFAULT_ICON_UNICODE = {
    "back": "◀️",
    "close": "✖️",
    "cancel": "🚫",
    "noop": "▫️",
    "copy": "📋",
    "next": "➡️",
    "prev": "⬅️",
    "settings": "⚙️",
    "help": "❓",
    "download": "⬇️",
    "play": "▶️",
    "pause": "⏸️",
    "redirect": "🔗",
    "search": "🔍",
    "person": "👤",
    "game": "🎮",
    "buy": "💳",
    "lock": "🔒",
    "location": "📍",
    "phone": "📞",
    "poll": "📊",
    "lang": "🌐",
    "home": "🏠",
}

TG_ANDROID_ICON_DOCUMENT_IDS = {name: None for name in DEFAULT_ICON_UNICODE}

_WORD_ICON_NAMES = {name: name for name in DEFAULT_ICON_UNICODE}
_WORD_ICON_NAMES.update(
    {
        "previous": "prev",
        "games": "game",
        "language": "lang",
        "languages": "lang",
        "start": "home",
        "mainmenu": "home",
        "main": "home",
    }
)

_TYPE_ICON_NAMES = {
    "url": "redirect",
    "web_app": "redirect",
    "webapp": "redirect",
    "simple_web_app": "redirect",
    "simple_webapp": "redirect",
    "switch_inline": "search",
    "user": "person",
    "request_user": "person",
    "chat": "person",
    "request_chat": "person",
    "broadcast": "person",
    "request_broadcast": "person",
    "game": "game",
    "buy": "buy",
    "auth": "lock",
    "location": "location",
    "request_location": "location",
    "phone": "phone",
    "request_phone": "phone",
    "poll": "poll",
    "request_poll": "poll",
}

_WORD_RE = re.compile(r"[a-z]+")


_BUTTON_TYPE_REGISTRY = button_kit._registry


register_button_type("copy", _build_copy_button)
register_button_type("toggle", _build_toggle_button)


_HAS_REQUEST_PEER = hasattr(Button, "request_peer")
if _HAS_REQUEST_PEER:
    _LOGS.debug(
        "custom.tg: Button.request_peer available - 'user' buttons use native request-peer."
    )
else:
    _LOGS.debug(
        "custom.tg: Button.request_peer absent on this Telethon build - "
        "'user' buttons deliberately fall back to inline callback buttons "
        "carrying the user id as callback data."
    )

_REQUEST_PEER_TYPES = {
    "user": "RequestPeerTypeUser",
    "chat": "RequestPeerTypeChat",
    "broadcast": "RequestPeerTypeBroadcast",
}


_WebViewButtonCls = getattr(_types, "KeyboardButtonWebView", None)
_SimpleWebViewButtonCls = getattr(_types, "KeyboardButtonSimpleWebView", None)
_WebViewDataSentCls = getattr(_types, "MessageActionWebViewDataSent", None)
_WebViewDataSentMeCls = getattr(_types, "MessageActionWebViewDataSentMe", None)
_WebViewResultSentCls = getattr(_types, "UpdateWebViewResultSent", None)
_WebViewResultUrlCls = getattr(_types, "WebViewResultUrl", None)
_SendWebViewResultMsgCls = getattr(
    _functions.messages, "SendWebViewResultMessageRequest", None
)

_HAS_WEBVIEW_BUTTONS = (
    _WebViewButtonCls is not None and _SimpleWebViewButtonCls is not None
)
_HAS_WEBVIEW_DATA_ACTIONS = (
    _WebViewDataSentCls is not None and _WebViewDataSentMeCls is not None
)
_HAS_WEBVIEW_RESULT_SENT = _WebViewResultSentCls is not None
_HAS_WEBVIEW_RESULT_MSG = _SendWebViewResultMsgCls is not None

if _HAS_WEBVIEW_BUTTONS:
    _LOGS.debug(
        "custom.tg: web_app button TL types available - "
        "('text', 'web_app', url) builds KeyboardButtonWebView."
    )
else:
    _LOGS.debug(
        "custom.tg: web_app button TL types absent on this Telethon build - "
        "web_app buttons deliberately fall back to URL/inline buttons."
    )
