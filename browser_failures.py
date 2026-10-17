"""Transport failures are not evidence of an invalid account or exhausted quota."""
import asyncio


class BrowserNotSubmitted(RuntimeError):
    retry_after = 30


class BrowserIdentityMismatch(RuntimeError):
    """An explicitly observed different identity, not a missing page element."""


def transient_transport_error(error):
    from curl_cffi.curl import CurlError
    from curl_cffi.requests.exceptions import ConnectionError, Timeout
    from playwright.async_api import TimeoutError as PageTimeout, Error as PageError
    return (isinstance(error, (asyncio.TimeoutError, ConnectionError, Timeout, PageTimeout, PageError))
            or isinstance(error, CurlError) and getattr(error, 'code', None) in {5,6,7,18,28,35,52,55,56,60,92})


def check_identity(labels, expected):
    import re
    emails = {email.casefold() for label in labels
              for email in re.findall(r'\(([^\s()@]+@[^\s()@]+)\)', label)}
    if expected.casefold() in emails:
        return True
    if emails:
        raise BrowserIdentityMismatch('Observed browser account differs from enrolled identity; not submitted')
    return False
