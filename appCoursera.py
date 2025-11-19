#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Playwright workflow runner (sync, Chromium persistent profile)

- Reads a JSON workflow (list of steps) and executes them in order.
- Supported step types:
    - "goto": open a URL (value or url key)
    - "click": find an element (by tag/attr/value/class/text) and click it
               (supports array_select_one to pick index when multiple)
    - "array": find multiple parent elements (by tag/class/attr/value),
               optionally filter by inner text (if_find_text_inside),
               then within each parent click child matchers listed in "click" array
    - "frame": switch to an iframe (by selector, name, or URL)
    - "main_frame": switch back to the main frame
    - "condition": execute steps based on conditions
    - "write": type text with random delays
    - "use_last_tab": switch to the last opened tab
    - "scroll": scroll to element
- All logs are in English and saved to workflow.log. On any failure the run stops.
- Tolerant to minor key typos like "Title" and "arrt".
"""

import argparse
import ctypes
import json
import logging
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# ------------------ Logging ------------------
LOG_FILE = "workflow.log"
logger = logging.getLogger("workflow")
logger.setLevel(logging.INFO)
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
ch = logging.StreamHandler()
fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
fh.setFormatter(fmt)
ch.setFormatter(fmt)
logger.addHandler(fh)
logger.addHandler(ch)


# ------------------ Desktop size detection ------------------
def get_desktop_size() -> Tuple[int, int]:
    """Cross-platform best-effort screen size detection."""
    try:
        user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        if user32:
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        else:
            import subprocess

            wh = subprocess.check_output(
                "xrandr | grep '*' | awk '{print $1}'", shell=True
            )
            w, h = map(int, wh.decode().strip().split("x"))
            return w, h
    except Exception:
        # Fallback
        return 1366, 768


# ------------------ Human typing (optional utility) ------------------
def human_type(element, text: str):
    """Type like a human: small random delays; slow down on spaces."""
    for ch in text:
        element.type(ch)
        extra = random.randint(200, 600) / 1000 if ch == " " else 0
        time.sleep(random.randint(50, 250) / 1000 + extra)


# ------------------ Helpers ------------------
def get_key(d: Dict[str, Any], key: str, *alts: str, default=None):
    """Fetch d[key] with tolerant aliasing (e.g., attr/arrt/attribute)."""
    if key in d:
        return d[key]
    for a in alts:
        if a in d:
            return d[a]
    # Fix common case-insensitive
    for k in d.keys():
        if k.lower() == key.lower():
            return d[k]
    return default


def to_int_or_none(x) -> Optional[int]:
    if x is None:
        return None
    try:
        return int(x)
    except Exception:
        return None


def normalize_class_selector(cls_value: Optional[str]) -> str:
    """Return CSS class part like '.c1.c2' or '' if none."""
    if not cls_value:
        return ""
    s = cls_value.strip()
    if s.startswith("."):
        # could be ".c1.c2" already
        return s
    # allow space-separated classes
    parts = [p for p in s.split() if p]
    return "." + ".".join(parts) if parts else ""


def build_css_selector(
    tag: Optional[str],
    cls: Optional[str],
    attr: Optional[str],
    value: Optional[str],
) -> str:
    """Build a robust CSS selector from parts."""
    t = (tag or "*").strip()
    c = normalize_class_selector(cls)
    a = ""
    if attr and value is not None:
        a = f'[{attr}="{value}"]'
    elif attr:
        a = f"[{attr}]"
    return f"{t}{c}{a}"


def wait_and_click(
    loc, index: int = 0, timeout: float = 15000, ignore_error: bool = False
):
    try:
        count = loc.count()
        if count == 0:
            if ignore_error:
                logger.warning("🚫 No matching elements found, but ignoring error.")
                return False
            else:
                raise RuntimeError("🚫 No matching elements found.")

        if index < 0 or index >= count:
            if ignore_error:
                logger.warning(
                    f"🚫 array_select_one index {index} is out of range (found {count}), but ignoring error."
                )
                return False
            else:
                raise RuntimeError(
                    f"array_select_one index {index} is out of range (found {count})."
                )

        target = loc.nth(index)
        target.wait_for(state="visible", timeout=timeout)
        target.scroll_into_view_if_needed()

        # ذخیره وضعیت قبل از کلیک (آیا المان href دارد؟)
        is_link = False
        try:
            is_link = bool(target.get_attribute("href"))
        except:
            pass  # نادیده گرفتن خطا در صورت منقضی بودن المان

        # اجرای کلیک
        target.click(timeout=timeout)

        # اگر المان لینک بود، منتظر ناوبری شویم
        if is_link:
            # روش هوشمندانه‌تر: صبر برای تغییر URL یا وضعیت لود
            try:
                page = target.page
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                time.sleep(2)  # فول‌بک در صورت خطا
        return True

    except Exception as e:
        if ignore_error:
            logger.warning(f"⚠️ Click failed but ignoring: {str(e).split(':')[0]}")
            return False
        else:
            raise RuntimeError(
                f"Element interaction failed: {str(e).split(':')[0]}"
            ) from e


def step_sleep(seconds: Optional[float]):
    if seconds is None:
        return
    try:
        s = float(seconds)
    except Exception:
        s = 0
    if s > 0:
        time.sleep(s)


# ------------------ Condition Checking ------------------
def check_condition(page, condition: Dict[str, Any], current_frame=None) -> bool:
    """
    Check a condition based on element presence/absence.
    Supported condition types:
    - "status": "found" or "not_found"
    - "tag", "attr", "value", "class", "text": element selector parameters
    """
    status = get_key(condition, "status")
    tag = get_key(condition, "tag")
    attr = get_key(condition, "attr", "arrt", "attribute")
    value = get_key(condition, "value")
    cls = get_key(condition, "class")
    text = get_key(condition, "text")

    if not status:
        raise RuntimeError('Condition missing "status" (found/not_found)')

    selector = build_css_selector(tag, cls, attr, value)

    # Use frame locator if we're in a frame context, otherwise use page
    if current_frame:
        loc = current_frame.locator(selector)
    else:
        loc = page.locator(selector)

    if text:
        loc = loc.filter(has_text=text)

    count = loc.count()

    logger.info(
        f"🔍 Condition check: {selector} status={status}, found={count} elements"
    )

    if status == "found":
        return count > 0
    elif status == "not_found":
        return count == 0
    else:
        raise RuntimeError(f'Unknown condition status: "{status}"')


# ------------------ Frame Management ------------------
def switch_to_frame(page, step: Dict[str, Any]):
    """
    Switch to an iframe based on selector, name, or URL.
    Supports:
    - "selector": CSS selector for the iframe
    - "name": name attribute of the iframe
    - "url": URL of the iframe (or partial match)
    - "index": numerical index of the iframe
    """
    frame_selector = get_key(step, "selector")
    frame_name = get_key(step, "name")
    frame_url = get_key(step, "url")
    frame_index = to_int_or_none(get_key(step, "index"))

    if frame_selector:
        logger.info(f"🖼️ Switching to frame by selector: {frame_selector}")
        frame = page.frame_locator(frame_selector)
        return frame
    elif frame_name:
        logger.info(f"🖼️ Switching to frame by name: {frame_name}")
        frame = page.frame(name=frame_name)
        if not frame:
            raise RuntimeError(f"Frame with name '{frame_name}' not found.")
        return frame
    elif frame_url:
        logger.info(f"🖼️ Switching to frame by URL: {frame_url}")
        # Wait for frame with matching URL
        for frame in page.frames:
            if frame_url in frame.url:
                return frame
        raise RuntimeError(f"Frame with URL containing '{frame_url}' not found.")
    elif frame_index is not None:
        logger.info(f"🖼️ Switching to frame by index: {frame_index}")
        frames = page.frames
        if frame_index < 0 or frame_index >= len(frames):
            raise RuntimeError(
                f"Frame index {frame_index} out of range (0-{len(frames) - 1})"
            )
        return frames[frame_index]
    else:
        raise RuntimeError(
            'Frame step requires one of: "selector", "name", "url", or "index"'
        )


def switch_to_main_frame(page):
    """Switch back to the main frame."""
    logger.info("🏠 Switching back to main frame")
    # In Playwright, we're automatically in the main frame when we don't specify a frame
    return None


# ------------------ Step executors ------------------
def exec_step_goto(page, step: Dict[str, Any]) -> None:
    url = get_key(step, "value", "url")
    if not url:
        raise RuntimeError('Missing "value" or "url" for goto step.')
    logger.info(f"🌐 Navigating to: {url}")
    page.goto(url)
    step_sleep(get_key(step, "sleep"))


def exec_step_click(page, step: Dict[str, Any], current_frame=None) -> None:
    # Check condition first
    condition = get_key(step, "if")
    if condition:
        condition_met = check_condition(page, condition, current_frame)
        logger.info(f"🔍 Condition check result: {condition_met}")

        if condition_met:
            # Execute alternative click steps
            alt_clicks = get_key(condition, "click", default=[])
            if not isinstance(alt_clicks, list):
                alt_clicks = [alt_clicks]

            for alt_click in alt_clicks:
                if not isinstance(alt_click, dict):
                    continue

                logger.info("🔄 Executing alternative click due to condition")
                # Recursively execute click step with alternative configuration
                exec_step_click(page, alt_click, current_frame)
            return  # Don't execute main click if condition was met and alternative executed

    # Proceed with normal click execution if no condition or condition not met
    tag = get_key(step, "tag")
    attr = get_key(step, "attr", "arrt", "attribute")
    value = get_key(step, "value")
    cls = get_key(step, "class")
    text = get_key(step, "text")
    idx = to_int_or_none(get_key(step, "array_select_one"))
    ignore_error = get_key(step, "ignore", default=False)

    selector = build_css_selector(tag, cls, attr, value)

    # Use frame locator if we're in a frame context, otherwise use page
    if current_frame:
        loc = current_frame.locator(selector)
    else:
        loc = page.locator(selector)

    if text:
        loc = loc.filter(has_text=text)

    logger.info(f"🔘 Click selector: {selector}{' | has_text=' + text if text else ''}")
    try:
        if idx is None:
            idx = 0
        success = wait_and_click(
            loc,
            index=idx,
            timeout=float(get_key(step, "timeout", default=45000)),
            ignore_error=ignore_error,
        )
        if not success and ignore_error:
            return  # If we're ignoring errors and click failed, just return
    except PWTimeout as e:
        if ignore_error:
            logger.warning(f"⚠️ Timeout waiting for element but ignoring: {selector}")
            return
        else:
            raise RuntimeError(f"Timeout waiting for element: {selector}") from e

    step_sleep(get_key(step, "sleep"))


def exec_step_write(page, step: Dict[str, Any], current_frame=None) -> None:
    """Type text with human-like delays."""
    text = get_key(step, "write", "value", "text")
    if not text:
        raise RuntimeError('Missing "write" or "value" for write step.')

    tag = get_key(step, "tag")
    attr = get_key(step, "attr", "arrt", "attribute")
    value = get_key(step, "value")
    cls = get_key(step, "class")
    text_filter = get_key(step, "text")
    idx = to_int_or_none(get_key(step, "array_select_one"))
    ignore_error = get_key(step, "ignore", default=False)

    selector = build_css_selector(tag, cls, attr, value)

    # Use frame locator if we're in a frame context, otherwise use page
    if current_frame:
        loc = current_frame.locator(selector)
    else:
        loc = page.locator(selector)

    if text_filter:
        loc = loc.filter(has_text=text_filter)

    logger.info(f"⌨️ Writing '{text}' to selector: {selector}")

    try:
        if idx is None:
            idx = 0

        count = loc.count()
        if count == 0:
            if ignore_error:
                logger.warning(
                    f"⚠️ No elements found for writing but ignoring: {selector}"
                )
                return
            else:
                raise RuntimeError(f"No elements found for writing: {selector}")

        if idx < 0 or idx >= count:
            if ignore_error:
                logger.warning(
                    f"⚠️ array_select_one index {idx} is out of range (found {count}), but ignoring error."
                )
                return
            else:
                raise RuntimeError(
                    f"array_select_one index {idx} is out of range (found {count})."
                )

        target = loc.nth(idx)
        target.wait_for(
            state="visible", timeout=float(get_key(step, "timeout", default=15000))
        )

        # Scroll to element
        target.scroll_into_view_if_needed()

        # Click to focus and clear if needed
        target.click()
        if get_key(step, "clear", default=True):
            target.clear()

        # Type with human-like delays
        human_type(target, text)

    except Exception as e:
        if ignore_error:
            logger.warning(f"⚠️ Write failed but ignoring: {e}")
        else:
            raise


def exec_step_array(page, step: Dict[str, Any], current_frame=None) -> None:
    """
    Find multiple parent elements by tag/class/attr/value,
    optionally filter by inner text (if_find_text_inside),
    then for each (or selected one) click child matchers defined in 'click' list.
    """
    tag = get_key(step, "tag")
    attr = get_key(step, "attr", "arrt", "attribute")
    value = get_key(step, "value")
    cls = get_key(step, "class")
    filter_text = get_key(step, "if_find_text_inside")
    parent_idx = to_int_or_none(get_key(step, "array_select_one"))  # optional
    ignore_error = get_key(step, "ignore", default=False)

    parent_selector = build_css_selector(tag, cls, attr, value)

    # Use frame locator if we're in a frame context, otherwise use page
    if current_frame:
        parents = current_frame.locator(parent_selector)
    else:
        parents = page.locator(parent_selector)

    if filter_text:
        parents = parents.filter(has_text=filter_text)

    total = parents.count()
    if total == 0:
        if ignore_error:
            logger.warning(
                f"⚠️ No parent elements found but ignoring: {parent_selector}"
            )
            return
        else:
            raise RuntimeError(
                f"No parent elements found for selector: {parent_selector} "
                f"{'with text: ' + filter_text if filter_text else ''}"
            )
    logger.info(f"🔍 Found {total} parent element(s) for: {parent_selector}")

    # Select which parents to process
    parent_indices: List[int]
    if parent_idx is not None:
        if parent_idx < 0 or parent_idx >= total:
            if ignore_error:
                logger.warning(
                    f"⚠️ array_select_one index {parent_idx} is out of range (found {total}), but ignoring error."
                )
                return
            else:
                raise RuntimeError(
                    f"array_select_one index {parent_idx} is out of range (found {total})."
                )
        parent_indices = [parent_idx]
    else:
        parent_indices = list(range(total))

    clicks: List[Dict[str, Any]] = get_key(step, "click", default=[])
    if not isinstance(clicks, list) or not clicks:
        raise RuntimeError('Missing non-empty "click" array for array step.')

    # For each selected parent, run the child clicks in order
    for i in parent_indices:
        p = parents.nth(i)
        logger.info(f"🔄 Processing parent index {i}...")
        for j, child in enumerate(clicks, start=1):
            ctag = get_key(child, "tag")
            ctext = get_key(child, "text")
            cattr = get_key(child, "attr", "arrt", "attribute")
            cvalue = get_key(child, "value")
            ccls = get_key(child, "class")
            csleep = get_key(child, "sleep")
            cignore = get_key(child, "ignore", default=False)

            child_selector = build_css_selector(ctag, ccls, cattr, cvalue)
            child_loc = p.locator(child_selector)
            if ctext:
                child_loc = child_loc.filter(has_text=ctext)

            logger.info(
                f"  🔘 Child click [{j}]: {child_selector}{' | has_text=' + ctext if ctext else ''}"
            )
            try:
                success = wait_and_click(
                    child_loc,
                    index=0,
                    timeout=float(get_key(step, "timeout", default=15000)),
                    ignore_error=cignore,
                )
                if not success and cignore:
                    continue  # If we're ignoring errors and click failed, continue to next child
            except PWTimeout as e:
                if cignore:
                    logger.warning(
                        f"⚠️ Timeout waiting for child element but ignoring: {child_selector}"
                    )
                    continue
                else:
                    raise RuntimeError(
                        f"Timeout waiting for child element: {child_selector}"
                    ) from e
            step_sleep(csleep)

    step_sleep(get_key(step, "sleep"))


def exec_step_frame(page, step: Dict[str, Any]):
    """Switch to an iframe."""
    return switch_to_frame(page, step)


def exec_step_main_frame(page, step: Dict[str, Any]):
    """Switch back to the main frame."""
    switch_to_main_frame(page)
    return None


def exec_step_use_last_tab(browser, step: Dict[str, Any]):
    """Switch to the last opened tab."""
    tabs = browser.pages
    if len(tabs) > 1:
        last_tab = tabs[-1]
        last_tab.bring_to_front()
        logger.info(f"📑 Switched to last tab: {last_tab.url}")
    else:
        logger.info("ℹ️ Only one tab open, no switch needed.")
    step_sleep(get_key(step, "sleep"))


def exec_step_scroll(page, step: Dict[str, Any], current_frame=None) -> None:
    """Scroll to an element or by position."""
    tag = get_key(step, "tag")
    attr = get_key(step, "attr", "arrt", "attribute")
    value = get_key(step, "value")
    cls = get_key(step, "class")
    text = get_key(step, "text")
    idx = to_int_or_none(get_key(step, "array_select_one"))
    ignore_error = get_key(step, "ignore", default=False)

    # Check if it's a position scroll
    x = get_key(step, "x")
    y = get_key(step, "y")

    if x is not None or y is not None:
        # Position-based scrolling
        x_pos = int(x) if x is not None else 0
        y_pos = int(y) if y is not None else 0
        logger.info(f"📜 Scrolling to position: x={x_pos}, y={y_pos}")
        page.evaluate(f"window.scrollTo({x_pos}, {y_pos})")
        return

    # Element-based scrolling
    if not any([tag, attr, value, cls, text]):
        raise RuntimeError(
            "Scroll step requires either position (x,y) or element selector"
        )

    selector = build_css_selector(tag, cls, attr, value)

    # Use frame locator if we're in a frame context, otherwise use page
    if current_frame:
        loc = current_frame.locator(selector)
    else:
        loc = page.locator(selector)

    if text:
        loc = loc.filter(has_text=text)

    logger.info(f"📜 Scroll to selector: {selector}")

    try:
        if idx is None:
            idx = 0

        count = loc.count()
        if count == 0:
            if ignore_error:
                logger.warning(
                    f"⚠️ No elements found for scrolling but ignoring: {selector}"
                )
                return
            else:
                raise RuntimeError(f"No elements found for scrolling: {selector}")

        if idx < 0 or idx >= count:
            if ignore_error:
                logger.warning(
                    f"⚠️ array_select_one index {idx} is out of range (found {count}), but ignoring error."
                )
                return
            else:
                raise RuntimeError(
                    f"array_select_one index {idx} is out of range (found {count})."
                )

        target = loc.nth(idx)
        target.wait_for(
            state="visible", timeout=float(get_key(step, "timeout", default=15000))
        )
        target.scroll_into_view_if_needed()
        logger.info("✅ Scrolled to element successfully")

    except Exception as e:
        if ignore_error:
            logger.warning(f"⚠️ Scroll failed but ignoring: {e}")
        else:
            raise


# ------------------ Runner ------------------
def run(
    workflow: List[Dict[str, Any]],
    start_url: Optional[str] = None,
    profile_dir: Optional[str] = None,
):
    width, height = get_desktop_size()
    profile = profile_dir or os.path.join(os.getcwd(), "pw_profile")

    logger.info("🚀 === Starting workflow run ===")
    logger.info(f"📁 Profile dir: {profile}")
    logger.info(f"🖥️ Viewport: {width}x{height}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
            args=[f"--window-size={width},{height}", "--start-maximized"],
            viewport={"width": width, "height": height},
            screen={"width": width, "height": height},
        )
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            current_frame = None  # Track current frame context

            # Optional initial URL
            if start_url:
                logger.info(f"🌐 Initial goto: {start_url}")
                page.goto(start_url)

            # Execute steps
            for idx, step in enumerate(workflow, start=1):
                title = get_key(step, "title", "Title", default=f"Step #{idx}")
                stype = get_key(step, "type")
                ignore_error = get_key(step, "ignore", default=False)

                logger.info(f"--- Step {idx}: {title} ---")
                print(f"📝 [Step {idx}] {title}")

                if not stype:
                    if ignore_error:
                        logger.warning("⚠️ Missing 'type' in step, but ignoring error.")
                        continue
                    else:
                        raise RuntimeError('Missing "type" in step.')

                stype_l = str(stype).strip().lower()

                try:
                    if stype_l == "goto":
                        exec_step_goto(page, step)
                        current_frame = None  # Reset frame context after navigation
                    elif stype_l == "click":
                        exec_step_click(page, step, current_frame)
                    elif stype_l == "array":
                        exec_step_array(page, step, current_frame)
                    elif stype_l == "frame":
                        current_frame = exec_step_frame(page, step)
                    elif stype_l == "main_frame":
                        current_frame = exec_step_main_frame(page, step)
                    elif stype_l == "write":
                        exec_step_write(page, step, current_frame)
                    elif stype_l == "use_last_tab":
                        exec_step_use_last_tab(browser, step)
                    elif stype_l == "scroll":
                        exec_step_scroll(page, step, current_frame)
                    else:
                        if ignore_error:
                            logger.warning(
                                f"⚠️ Unsupported step type but ignoring: '{stype}'"
                            )
                        else:
                            raise RuntimeError(f'Unsupported step type: "{stype}"')
                except Exception as e:
                    if ignore_error:
                        logger.warning(f"⚠️ Step failed but ignoring: {title} | {e}")
                        print(f"⚠️ [WARNING] {title}: {e}")
                    else:
                        logger.error(f"❌ Step failed: {title} | {e}")
                        print(f"❌ [ERROR] {title}: {e}")
                        raise  # stop workflow immediately

            logger.info("✅ === Workflow completed successfully ===")
            print("✅ Workflow completed successfully.")

        finally:
            # Give user a moment to see state (optional). Comment out if not needed.
            input("⏸️ Press Enter to close the browser...")
        # browser.close()


# ------------------ CLI ------------------
def main():
    parser = argparse.ArgumentParser(
        description="Run a browser workflow from a JSON file."
    )
    parser.add_argument(
        "--workflow", required=True, help="Path to the workflow JSON file."
    )
    parser.add_argument("--url", help="Optional initial URL to open before steps.")
    parser.add_argument("--profile", help="Optional persistent profile directory.")
    args = parser.parse_args()

    # Load JSON
    try:
        with open(args.workflow, "r", encoding="utf-8") as f:
            wf = json.load(f)
        if not isinstance(wf, list):
            raise ValueError("Workflow must be a JSON array (list of steps).")
    except Exception as e:
        logger.error(f"❌ Failed to load workflow JSON: {e}")
        print(f"❌ [ERROR] Failed to load workflow JSON: {e}")
        sys.exit(1)

    try:
        run(wf, start_url=args.url, profile_dir=args.profile)
    except Exception as e:
        logger.error(f"❌ Run aborted: {e}")
        print(f"❌ [FATAL] Run aborted: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
