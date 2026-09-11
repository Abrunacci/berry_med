# Certificados reales para `tests/test_cert_check.py`

Públicos, bajados el 2026-09-10. Reproducen el incidente de septiembre de 2026
(ver `docs/certificado_vencido.md`).

| Archivo | Qué es |
|---|---|
| `portal_hoja.pem`, `ye1.pem`, `root_ye_cruzado.pem`, `isrg_root_x2_cruzado_2032.pem` | La cadena que mandaba `portal.ondoctor365.com`, en ese orden. |
| `isrg_root_x1.pem` | Raíz ISRG Root X1 (vence 2035-06-04). |
| `isrg_root_x2.pem` | Raíz ISRG Root X2 autofirmada (vence 2040). |
| `isrg_root_x2_cruzado_vencido.pem` | El cruce viejo "ISRG Root X2" emitido por "ISRG Root X1", vencido el 2025-09-15 (SHA-1 `151682F5…`). Es el que estaba en el almacén CA de SUBSUELO-11. |

La hoja del portal vence el 2026-11-06: los tests usan una fecha fija, así que
eso no los rompe.
