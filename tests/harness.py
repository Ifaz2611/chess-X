"""Integration test harness for Chess-X grabbers.

Provides an in-memory, DOM-aware simulated WebDriver and WebElement
implementation that parses static HTML snapshots without requiring a live
browser, display server, or network connection.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By


class MockWebElement:
    """Mock Selenium WebElement backed by a parsed HTML node."""

    def __init__(self, tag: str, attrs: dict[str, str], parent: MockWebElement | None = None):
        self.tag_name = tag.lower()
        self.attrs = {k.lower(): v for k, v in attrs.items()}
        self.parent = parent
        self.children: list[MockWebElement] = []
        self._raw_text_parts: list[str] = []
        self._location = {"x": 100, "y": 150}
        self._size = {"width": 640, "height": 640}

    @property
    def text(self) -> str:
        parts = []
        for p in self._raw_text_parts:
            parts.append(p)
        for c in self.children:
            t = c.text
            if t:
                parts.append(t)
        return " ".join("".join(parts).split())

    @property
    def inner_text(self) -> str:
        return self.text

    def get_attribute(self, name: str) -> str | None:
        n = name.lower()
        if n == "class":
            return self.attrs.get("class", "")
        return self.attrs.get(n, None)

    @property
    def location(self) -> dict[str, int]:
        return dict(self._location)

    @property
    def size(self) -> dict[str, int]:
        return dict(self._size)

    def is_displayed(self) -> bool:
        style = self.attrs.get("style", "")
        if "display:none" in style.replace(" ", "") or "visibility:hidden" in style.replace(" ", ""):
            return False
        return "hidden" not in self.attrs

    def click(self) -> None:
        pass

    def _matches_css_simple(self, selector: str) -> bool:
        sel = selector.strip()
        if not sel:
            return False
        # Handle comma-separated list
        if "," in sel:
            return any(self._matches_css_simple(part.strip()) for part in sel.split(","))

        # Check attributes e.g. [data-node], [data-test-element='move-list'], [class*='board']
        attr_patterns = re.findall(r"\[([^\]]+)\]", sel)
        for attr_spec in attr_patterns:
            if "*=" in attr_spec:
                k, v = attr_spec.split("*=", 1)
                k = k.strip().lower()
                v = v.strip().strip("'\"")
                if v not in self.attrs.get(k, ""):
                    return False
            elif "=" in attr_spec:
                k, v = attr_spec.split("=", 1)
                k = k.strip().lower()
                v = v.strip().strip("'\"")
                if self.attrs.get(k) != v:
                    return False
            else:
                k = attr_spec.strip().lower()
                if k not in self.attrs:
                    return False

        # Strip attribute brackets for tag, id, and class matching
        sel_core = re.sub(r"\[[^\]]+\]", "", sel).strip()
        if not sel_core:
            return True

        tag = None
        elem_id = None
        classes = []

        # Parse ID
        if "#" in sel_core:
            parts = sel_core.split("#", 1)
            sel_without_id = parts[0]
            rest = parts[1]
            if "." in rest:
                id_parts = rest.split(".", 1)
                elem_id = id_parts[0]
                classes.extend(id_parts[1].split("."))
            else:
                elem_id = rest
            sel_core = sel_without_id

        # Parse classes and tag
        if "." in sel_core:
            c_parts = sel_core.split(".")
            tag = c_parts[0] if c_parts[0] else None
            classes.extend(c_parts[1:])
        else:
            tag = sel_core if sel_core else None

        if tag and tag != "*" and self.tag_name != tag.lower():
            return False
        if elem_id and self.attrs.get("id") != elem_id:
            return False
        if classes:
            my_classes = self.attrs.get("class", "").split()
            for c in classes:
                if c and c not in my_classes:
                    return False
        return True

    def find_elements(self, by: str, value: str) -> list[MockWebElement]:
        results: list[MockWebElement] = []

        if by == By.TAG_NAME:
            target = value.lower()
            self._walk(lambda n: results.append(n) if n.tag_name == target else None)
        elif by == By.ID:
            self._walk(lambda n: results.append(n) if n.attrs.get("id") == value else None)
        elif by == By.CLASS_NAME:
            self._walk(lambda n: results.append(n) if value in n.attrs.get("class", "").split() else None)
        elif by == By.CSS_SELECTOR:
            if "," in value:
                for part in value.split(","):
                    p = part.strip()
                    if p:
                        results.extend(self.find_elements(By.CSS_SELECTOR, p))
            else:
                # Handle descendant selector: parent child
                parts = [p.strip() for p in value.split() if p.strip()]
                if len(parts) == 1:
                    self._walk(lambda n: results.append(n) if n._matches_css_simple(parts[0]) else None)
                elif len(parts) > 1:
                    first = parts[0]
                    rest = " ".join(parts[1:])
                    ancestors: list[MockWebElement] = []
                    self._walk(lambda n: ancestors.append(n) if n._matches_css_simple(first) else None)
                    for a in ancestors:
                        results.extend(a.find_elements(By.CSS_SELECTOR, rest))
        elif by == By.XPATH:
            val = value.strip()
            # 1. Descendant wildcard
            if val in (".//*", "//*", "*"):
                self._walk(lambda n: results.append(n))
                return results

            # 2. SVG name() query
            if "name()='svg'" in val or 'name()="svg"' in val or ("svg" in val.lower() and "name()" in val):
                self._walk(lambda n: results.append(n) if n.tag_name == "svg" else None)
                return results

            # 3. Contains matching e.g. //button[contains(., 'Next')] or //div[contains(@class,'move-list')]
            if "contains(" in val:
                contains_match = re.search(r"//([a-zA-Z0-9_\-\*]*)\[contains\(([^,]+),\s*['\"]([^'\"]+)['\"]\)", val)
                if contains_match:
                    tag, field, target_str = contains_match.groups()
                    target_tag = None if tag in ("*", "", None) else tag.lower()
                    target_str = target_str.strip()
                    field = field.strip()

                    def check_contains(n: MockWebElement):
                        if target_tag and n.tag_name != target_tag:
                            return
                        if field == ".":
                            if target_str.lower() in n.text.lower():
                                results.append(n)
                        elif field.startswith("@"):
                            attr_name = field[1:].lower()
                            if target_str.lower() in n.attrs.get(attr_name, "").lower():
                                results.append(n)

                    self._walk(check_contains)
                    return results
                else:
                    target_str = re.findall(r"['\"]([^'\"]+)['\"]", val)
                    if target_str:
                        ts = target_str[0].lower()
                        self._walk(lambda n: results.append(n) if ts in n.text.lower() or any(ts in v.lower() for v in n.attrs.values()) else None)
                    return results

            # 4. Structural path matching e.g. /html/body/... or //*[@id='...']/main/...
            if "/" in val:
                segments = [s for s in val.split("/") if s]
                if not segments:
                    return []

                start_nodes: list[MockWebElement] = []
                first_seg = segments[0]
                idx = 0

                id_match = re.search(r"@id=['\"]([^'\"]+)['\"]", first_seg)
                if id_match:
                    target_id = id_match.group(1)
                    self._walk(lambda n: start_nodes.append(n) if n.attrs.get("id") == target_id else None)
                    idx = 1
                elif first_seg.lower() == "html":
                    root_elem = self
                    while root_elem.parent is not None:
                        root_elem = root_elem.parent
                    start_nodes = [root_elem]
                    idx = 1
                else:
                    clean_tag = re.sub(r"\[[^\]]+\]", "", first_seg).strip().lower()
                    if clean_tag and clean_tag != "*":
                        self._walk(lambda n: start_nodes.append(n) if n.tag_name == clean_tag else None)
                    else:
                        start_nodes = [self]
                    idx = 1

                curr_level = start_nodes
                for seg in segments[idx:]:
                    if not curr_level:
                        break
                    idx_match = re.search(r"\[(\d+)\]", seg)
                    target_idx = int(idx_match.group(1)) if idx_match else None
                    seg_tag = re.sub(r"\[[^\]]+\]", "", seg).strip().lower()

                    next_level = []
                    for parent_node in curr_level:
                        matching_children = [
                            c for c in parent_node.children
                            if seg_tag in ("*", "") or c.tag_name == seg_tag
                        ]
                        if target_idx is not None:
                            if 1 <= target_idx <= len(matching_children):
                                next_level.append(matching_children[target_idx - 1])
                        else:
                            next_level.extend(matching_children)
                    curr_level = next_level

                return curr_level

            # 5. Simple ID or tag lookup
            id_match = re.search(r"@id=['\"]([^'\"]+)['\"]", val)
            if id_match:
                target_id = id_match.group(1)
                self._walk(lambda n: results.append(n) if n.attrs.get("id") == target_id else None)
                return results

            clean = re.sub(r"[^a-zA-Z0-9_\-]", " ", val).split()
            if clean:
                last_w = clean[-1].lower()
                self._walk(lambda n: results.append(n) if n.tag_name == last_w else None)

        # Deduplicate while preserving order
        unique_results: list[MockWebElement] = []
        seen = set()
        for elem in results:
            eid = id(elem)
            if eid not in seen:
                seen.add(eid)
                unique_results.append(elem)
        return unique_results

    def find_element(self, by: str, value: str) -> MockWebElement:
        elems = self.find_elements(by, value)
        if not elems:
            raise NoSuchElementException(f"Cannot find element by {by}={value}")
        return elems[0]

    def _walk(self, callback) -> None:
        for child in self.children:
            callback(child)
            child._walk(callback)


class _DOMParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = MockWebElement("html", {})
        self.stack: list[MockWebElement] = [self.root]

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1]
        elem = MockWebElement(tag, dict(attrs), parent=parent)
        parent.children.append(elem)
        # Void elements in HTML
        if tag.lower() not in ("img", "br", "hr", "input", "meta", "link"):
            self.stack.append(elem)

    def handle_endtag(self, tag):
        t = tag.lower()
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag_name == t:
                self.stack = self.stack[:idx]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]._raw_text_parts.append(data)


class MockWebDriver:
    """Mock Remote WebDriver loaded with a static HTML snapshot."""

    def __init__(self, html_content: str, current_url: str = "https://www.chess.com/play/online"):
        self.current_url = current_url
        self.session_id = "test-session-1234"
        self._html = html_content
        parser = _DOMParser()
        parser.feed(html_content)
        self.root = parser.root

    @classmethod
    def from_file(cls, path: str, current_url: str = "https://www.chess.com/play/online") -> MockWebDriver:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return cls(content, current_url=current_url)

    @property
    def page_source(self) -> str:
        return self._html

    def find_elements(self, by: str, value: str) -> list[MockWebElement]:
        return self.root.find_elements(by, value)

    def find_element(self, by: str, value: str) -> MockWebElement:
        return self.root.find_element(by, value)

    def execute_script(self, script: str, *args) -> Any:
        s = script.strip()
        # Screen position queries
        if "window.screenX" in s or "window.screenY" in s:
            return 100
        # innerText queries
        if "document.body.innerText" in s:
            limit = args[0] if args and isinstance(args[0], int) else 2000
            return self.root.text[:limit]
        # Clock extraction queries
        if "clock-component" in s or "rclock" in s:
            clocks = []
            for sel in [".clock-component", ".clock-time-monospace", ".rclock-top .time", ".rclock-bottom .time", ".time"]:
                found = self.root.find_elements(By.CSS_SELECTOR, sel)
                if found:
                    clocks.extend([f.text for f in found if f.text])
            if len(clocks) >= 2:
                return clocks[:2]
            return []
        # Obfuscated / JS SAN extraction
        if "a1t" in s or "rm6" in s or "l4x" in s or "extract" in s or "move" in s:
            moves = []
            for node in self.root.find_elements(By.CSS_SELECTOR, "move"):
                t = node.text.strip()
                if t:
                    moves.append(t)
            if not moves:
                for node in self.root.find_elements(By.CSS_SELECTOR, ".node"):
                    t = node.text.strip()
                    if t:
                        moves.append(t)
            return moves
        # Click handler
        if "click" in s and args:
            return None
        return None
