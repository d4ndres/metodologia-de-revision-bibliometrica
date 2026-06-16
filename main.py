from datetime import datetime
import os

import pandas as pd
import pybliometrics
from dotenv import load_dotenv
from pybliometrics.scopus import ScopusSearch, AbstractRetrieval


QUERY = """
TITLE-ABS-KEY ( assistant system AND assembly AND iterative design )
""".strip()


def main() -> None:
    # Cargar variables del archivo .env
    load_dotenv()

    # Tomar la llave principal de Scopus desde el entorno
    api_key = os.getenv("ScopusSecretKey")

    if api_key:
        pybliometrics.init(keys=[api_key])
    else:
        pybliometrics.init()

    # Ejecutar la búsqueda
    search = ScopusSearch(
        QUERY,
        subscriber=True,
        view="COMPLETE",
        refresh=True,
        download=True,
        verbose=True,
    )

    results = search.results or []
    df = pd.DataFrame([result._asdict() for result in results])

    # Quitar duplicados si existe la columna eid
    if not df.empty and "eid" in df.columns:
        df = df.drop_duplicates(subset=["eid"])

    # Exportar a Excel
    filename = f"scopus_results_{datetime.now():%Y%m%d_%H%M}.xlsx"
    df.to_excel(filename, index=False)

    print(f"Resultados encontrados: {len(df)}")
    print(f"Archivo generado: {filename}")

    # Mostrar una vista previa simple
    preview_cols = [col for col in ["eid", "title", "coverDate", "doi"] if col in df.columns]
    if preview_cols:
        print(df[preview_cols].head())


if __name__ == "__main__":
    main()
