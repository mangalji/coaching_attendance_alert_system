from whitenoise.storage import CompressedManifestStaticFilesStorage


class LenientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Same as WhiteNoise's manifest storage, but never turns a missing manifest
    entry into a 500 on every page.

    By default Django/WhiteNoise raise ValueError from the {% static %} tag if
    `collectstatic` was never run, or was run against a different DEBUG/STORAGES
    configuration than the one serving the request (an easy mistake when deploying
    without Docker). Since every page includes app.css, that one missing entry would
    otherwise take the whole site down. Falling back to the plain filename means the
    page still renders — worst case with stale, unhashed static files until
    `collectstatic` is run correctly - instead of a hard outage.
    """

    manifest_strict = False
