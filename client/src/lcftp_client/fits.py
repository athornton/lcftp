"""FITS header provenance stamping."""

from pathlib import Path

from astropy.io import fits

__all__ = ["stamp_provenance"]


def stamp_provenance(
    path: Path,
    *,
    plan_id: str,
    item_id: str,
    observatory_id: str,
    dia_object_id: int,
    alert_id: str | None,
    driver: str,
    driver_version: str,
) -> None:
    """Write LCFTP provenance keywords into a frame's primary header.

    This changes the file, so checksums must be computed afterwards.
    """
    with fits.open(path, mode="update") as hdul:
        header = hdul[0].header
        header["LCFTPPLN"] = (plan_id, "LCFTP plan id")
        header["LCFTPITM"] = (item_id, "LCFTP plan item id")
        header["LCFTPOBS"] = (observatory_id, "LCFTP observatory id")
        header["LCFTPDIA"] = (dia_object_id, "Rubin diaObjectId")
        header["LCFTPALR"] = (alert_id or "", "Latest alert id")
        header["LCFTPDRV"] = (f"{driver}/{driver_version}", "Telescope driver")
