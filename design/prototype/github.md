repo: Yesenin33/Robin
branch: claude/sweet-ritchie-evn4zw

## Last sync
date: 2026-10-16T11:49:57Z
### Updated in this project
- Прочитаны маршруты App.tsx, все 6 экранов, ConnectGate, SwapSheet, WalletConnect, типы API, TASKS/opus-2026-10-16.md
- Дизайн пересобран как отдельные страницы со ссылками между ними, по реальным маршрутам и функциям
- Убрана плашка Plus; вход только через кошелёк

## Screen map
| Page (design) | Repo route / files |
|---|---|
| Landing.dc.html | / · screens/Landing.tsx |
| Login.dc.html | /login · screens/Onboarding.tsx, components/WalletConnect.tsx |
| Connect.dc.html | /app без кошелька · screens/ConnectGate.tsx |
| Portfolio.dc.html | /app/portfolio · screens/Portfolio.tsx |
| Safety.dc.html | /app/safety/:ca? · screens/Safety.tsx, components/SwapSheet.tsx, Sheet.tsx |
| Copy.dc.html | /app/copy · screens/Copy.tsx |
| Profile.dc.html | /app/profile · screens/Profile.tsx |
| AppNav.dc.html | components/TabBar.tsx (на десктопе боковое меню) |
| ProtoDock.dc.html | только для прототипа, в продукт не переносится |
