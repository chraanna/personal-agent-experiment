# Nästa steg för Shilpi

## 1. Spara påminnelser så de överlever omstart
Just nu försvinner alla påminnelser om servern startar om.
Lösning: spara påminnelser till en fil (som vi redan gör med kalender-tokens).

## 2. Lösa token-problemet på Railway
Kalender-tokens (inloggningen till Microsoft) försvinner vid varje deploy på Railway
eftersom Railway raderar filer. Lösning: använda Railways volymlagring (persistent storage)
eller spara tokens på annat sätt.

## 3. Få personlighetsval att fungera
Onboarding-sidan låter användaren välja personlighet (Humor, Lugn, Minimal osv.)
men valet gör ingenting just nu. Koppla ihop valet med hur Shilpi svarar.

## 4. Städa bort oanvända filer
- `style.css` — används inte (alla stilar ligger i index.html)
- `commitments.json` — gammal testfil som inget i koden läser

## 5. Framtida idéer
- Koppla in en AI-modell så Shilpi förstår fler typer av meddelanden
- Lägga till tester
