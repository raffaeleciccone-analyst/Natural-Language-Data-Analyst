# Immagini del README

Il README mostra due **anteprime del design**, non catture dell'app in esecuzione:

- `preview-report.svg` — report iniziale sui dati
- `preview-answer.svg` — risposta a una domanda, con grafico

Sono SVG disegnati a mano: riproducono l'aspetto reale dell'interfaccia (stessa
palette e stessa tipografia di `nlda/ui_theme.py`) ma restano una
rappresentazione. La distinzione è dichiarata anche nel README principale.

## Sostituirle con screenshot veri

1. Avvia l'app: `streamlit run main.py` — oppure `docker compose up`
2. Carica un dataset (o usa quello di default) e fai una domanda che produca un
   grafico
3. Cattura la finestra del browser a ~1280px di larghezza, salva le immagini in
   questa cartella e aggiorna i link nella sezione **Anteprima** del `README.md`

PNG o GIF vanno bene: GitHub li mostra inline. Una GIF che riprende una domanda
dall'inizio alla fine racconta il progetto meglio di qualsiasi immagine statica.
