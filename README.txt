AKTIEN APP v8 – MARKTSITUATION, ASSET-SCREENER, ENTRY & RISIKO

Diese Version setzt das Konzept aus "Aktien App.md" (Vault, Projekte/Finanzen/Aktien)
in drei Tabs um:

1. Marktsituation
   - Zinsentscheidungen (Fed, EZB, BoJ) inkl. nächster Sitzungstermine
   - US-Inflation, US-Zinskurve, US-Dollar-Index (DXY)
   - Wahltermine mit Marktbezug
   - Berichtssaison (Kalender-Heuristik)
   - Jahreszyklus/Saisonalität (Sell in May, Santa-Claus-Rally, US-Wahlzyklus, ...)
   - Kriege/geopolitische Konflikte (Basisliste + aktuelle Schlagzeilen)
   - Aktiensplits/Dividenden werden je Kandidat im Asset-Screener geprüft
   Ampel-Farben (rot/gelb/grün) je Faktor plus Gesamteinschätzung oben.

2. Asset-Screener
   - Lädt S&P 500, NASDAQ-100, DAX und CSI 300 (wie bisher)
   - Erkennt mehrfach getestete horizontale Supports/Widerstände, Handelskanäle,
     Kopf-Schulter- und inverse Kopf-Schulter-Muster, eine vollständige Elliott-
     Wellen-Zählung (Impuls 1–5 + Korrektur A-B-C mit Regelprüfung, siehe unten)
     sowie Ausbrüche über Widerstand / unter Support (mit Volumen-Check)
   - Stuft jeden Kandidaten als Long-Setup, Short-Setup oder "kein klares Setup" ein
   - Long-/Short-Kandidaten werden relativ zum aktuellen Marktuniversum gerankt

3. Entry, Chancen & Risiken
   - Zeigt je Kandidat Entry-Idee, Stop-Loss-Idee, Exit-/Kursziel und CRV
   - Erinnert daran, Stop-Loss- und Exit-Order direkt beim Broker zu setzen
     (die App setzt keine Order automatisch – nur Anzeige/Empfehlung)
   - "Order-Notiz in Zwischenablage kopieren" für den schnellen Rücksprung zum Broker
   - Chart zeigt Trendkanal, Swing-Punkte, SKS-Muster/Nackenlinie, Ausbruchspunkt und
     die aktuelle Elliott-Wellen-Zählung direkt eingezeichnet (0-1-2-3-4-5-A-B-C) –
     kein Wechsel zu TradingView mehr nötig, um zu sehen, wo die App das Setup sieht

Elliott-Wellen-Zählung im Detail:
   - Die App sucht rückwärts vom aktuellsten Kurspunkt das längste noch gültige
     Zählfenster (9 Punkte = kompletter Impuls 1–5 plus Korrektur A-B-C, bis runter
     auf 2 Punkte = "Welle 1 läuft").
   - Geprüft werden die drei harten Elliott-Regeln: Welle 2 darf Welle 1 nicht zu
     100 % zurücklaufen, Welle 3 darf nie die kürzeste Impulswelle sein, Welle 4 darf
     nicht ins Kursgebiet von Welle 1 laufen. Verletzt eine Zählung eine dieser
     Regeln, verwirft die App sie automatisch und probiert ein kürzeres Fenster.
   - Angezeigt wird immer die "aktuelle Position" (z. B. "Welle 5 läuft" oder
     "Korrektur A-B-C abgeschlossen – neuer Impuls könnte beginnen") in Tab 3 und im
     Chart selbst.
   - Wichtig: Elliott-Wellen-Zählung ist – wie in der Praxis üblich – immer auch
     Auslegungssache. Die App liefert eine nachvollziehbare, regelbasierte Näherung,
     keine unumstrittene "richtige" Zählung.

Start:
1. ZIP vollständig entpacken (inkl. Ordner "marktdaten" – nicht löschen/umbenennen).
2. Falls noch nicht installiert: installieren.bat ausführen.
3. starten.bat öffnen.

Automatischer Ablauf:
- Beim Programmstart werden Marktsituation und Asset-Screening automatisch ausgeführt.
- Bereits am selben Tag geladene Rohdaten werden standardmäßig wiederverwendet.
- Die Ergebnisse werden als CSV und Excel gespeichert.
- Danach zeigt das Programm direkt die Long-Kandidaten A.

Bewertung:
- Long-Kandidat A/B und Short-Kandidat sind eine relative Rangliste innerhalb des
  aktuellen Marktuniversums, keine Anlageberatung.
- Es gelten weiterhin Mindestanforderungen an Muster/Support-Tests, Trend, CRV und
  Liquidität. Liquidität allein kann ein technisch schwaches Setup nicht hochstufen.
- Die Chartmuster-Erkennung (Kopf-Schulter, Kanal, Elliott-Wellen, Ausbruch) ist eine
  pragmatische Heuristik, kein akademisch strenges Modell.
- Die Einstufung ist eine Vorauswahl und kein automatisches Kauf-/Verkaufssignal.

Marktsituations-Daten (Ordner "marktdaten"):
- zentralbank_termine.json: Fed-/EZB-/BoJ-Sitzungstermine + BoJ-Leitzins (manuell
  gepflegt, da keine stabile freie Live-API existiert). Einmal jährlich prüfen.
- wahltermine.json: markt­relevante Wahlen. Einmal jährlich prüfen/ergänzen.
- konflikte_basis.json: dauerhafte geopolitische Risikoherde als Basisliste, wird
  automatisch um tagesaktuelle Schlagzeilen ergänzt.
- Alle anderen Marktsituations-Werte (Fed-Leitzins, EZB-Leitzins, US-Inflation,
  Zinskurve, Dollar-Index) werden automatisch aus freien Quellen (FRED, ECB SDW,
  Yahoo Finance) geladen. Ist eine Quelle nicht erreichbar, zeigt die App das klar
  als "nicht abrufbar – manuell prüfen" an, statt abzustürzen.

Manuelle Optionen:
- "Alles jetzt aktualisieren" startet Marktsituation + Download + Screening erneut.
- Die automatische Ausführung beim Start kann oben deaktiviert werden.
- "Heute geladene Daten wiederverwenden" vermeidet unnötige Downloads.
