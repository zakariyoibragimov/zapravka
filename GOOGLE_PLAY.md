# Google Play Deployment

Сейчас приложение подготовлено как PWA для мобильного клиента. Это самый быстрый путь, чтобы довести текущий FastAPI-проект до публикации в Google Play без переписывания его в нативный Android.

## Что уже есть

- мобильный экран клиента: `/mobile`
- PWA manifest: `/manifest.webmanifest`
- service worker: `/sw.js`
- иконки приложения: `/static/icons/...`

## Что именно публикуется в Google Play

В Google Play нужно публиковать только клиентскую мобильную часть, то есть экран `/mobile`.

Схема проекта такая:

- клиентское приложение: идет в Google Play как Android-обёртка над `/mobile`
- касса: остается веб-страницей `/cashier` на сервере
- админка: остается веб-страницами `/admin/*` на сервере
- API и база данных: работают на сервере

То есть Google Play нужен только для клиента. Кассиру и администратору отдельное Android-приложение сейчас не требуется.

## Что нужно сделать перед публикацией

1. Развернуть проект на публичном домене с HTTPS.
2. Проверить, что `https://ВАШ-ДОМЕН/mobile` открывается без ошибок на Android.
3. Проверить installability в Chrome DevTools: manifest, service worker, HTTPS.
4. Подготовить Android package name, например `com.azsbonus.app`.
5. Сгенерировать signing key для Android App Bundle.
6. Связать домен и Android-приложение через `/.well-known/assetlinks.json`.

## Самый практичный путь в Play

Использовать Trusted Web Activity через Bubblewrap.

В репозитории уже есть helper-скрипт для Windows: [scripts/google_play_twa.ps1](scripts/google_play_twa.ps1).
Он умеет:

- проверить manifest/privacy/assetlinks по публичному URL;
- валидировать `PUBLIC_APP_URL`, `ANDROID_APP_PACKAGE`, `ANDROID_SHA256_CERT_FINGERPRINTS`;
- запустить `bubblewrap init` в папке Android-проекта;
- затем собрать обёртку через `bubblewrap build`.

### Установка Bubblewrap

```bash
npm install -g @bubblewrap/cli
```

На Windows удобнее запускать через helper:

```powershell
./scripts/google_play_twa.ps1 -Action check -BaseUrl https://ВАШ-ДОМЕН
./scripts/google_play_twa.ps1 -Action init
./scripts/google_play_twa.ps1 -Action build
```

### Инициализация Android-обёртки вручную

```bash
bubblewrap init --manifest https://ВАШ-ДОМЕН/manifest.webmanifest
```

Во время инициализации укажи:

- packageId: `com.azsbonus.app`
- app name: `АЗС Бонус`
- launcher name: `АЗС Бонус`
- start URL: `https://ВАШ-ДОМЕН/mobile`

### Сборка

```bash
bubblewrap build
```

На выходе будет Android App Bundle (`.aab`) или Android-проект, который можно открыть в Android Studio.

## Что грузить в Google Play Console

1. Создать приложение в Google Play Console.
2. Заполнить карточку приложения.
3. Загрузить `.aab`.
4. Добавить иконку 512x512, скриншоты, политику конфиденциальности.
5. Пройти разделы Data safety, App access, Content rating.
6. Отправить на internal testing, потом production.

## Что ещё желательно заменить перед релизом

- заменить SVG-иконки PWA на финальные PNG 192x192 и 512x512
- добавить splash screen assets
- включить production-домен и стабильную БД
- настроить Firebase/Crash reporting при необходимости

## Что уже готово в этом проекте

- маршрут клиента для Play: `/mobile`
- privacy policy: `/privacy`
- manifest: `/manifest.webmanifest`
- Digital Asset Links: `/.well-known/assetlinks.json`
- service worker: `/sw.js`
- helper для TWA-сборки на Windows: [scripts/google_play_twa.ps1](scripts/google_play_twa.ps1)

## Что ещё остаётся перед реальной публикацией

1. Развернуть проект на публичном HTTPS-домене.
2. Поставить реальный `PUBLIC_APP_URL` в `.env`.
3. Указать финальный `ANDROID_APP_PACKAGE`.
4. После генерации/выбора signing key заполнить `ANDROID_SHA256_CERT_FINGERPRINTS`.
5. Заменить текущие SVG-иконки на финальные PNG-иконки для Android/Play Console.
6. Собрать `.aab` и загрузить его в Google Play Console.

Без публичного HTTPS-домена можно проверить локальную готовность PWA, но нельзя честно завершить Play-ready TWA-публикацию.

## Важно

Google Play не загружает FastAPI-проект напрямую. В Play загружается Android App Bundle (`.aab`). Для текущего проекта правильный путь такой:

`FastAPI mobile web app -> PWA -> TWA/Bubblewrap -> .aab -> Google Play`

Для этого проекта точнее так:

`/mobile -> PWA -> TWA/Bubblewrap -> .aab -> Google Play`

А серверная часть остается как есть:

`/cashier + /admin + API + DB -> сервер/VPS/облако`