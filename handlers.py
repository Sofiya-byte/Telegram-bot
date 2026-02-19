from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import ContentType
from database import SessionLocal
from models import Product, Admin
from sqlalchemy.orm import Session
from pulp import LpProblem, LpMinimize, LpVariable, lpSum, LpBinary, value
import pandas as pd
import tempfile
import os
import re


class CartStates(StatesGroup):
    waiting_for_product = State()
    waiting_for_quantity = State()
    waiting_for_product_selection = State()
    waiting_for_remove_product = State()


class ExcelUploadStates(StatesGroup):
    waiting_for_file = State()


class UserSession:
    def __init__(self):
        self.cart = {}  # {product_name: {quantity: float}}
        self.active = True


sessions = {}


def get_session(user_id: int) -> UserSession:
    if user_id not in sessions:
        sessions[user_id] = UserSession()
    return sessions[user_id]


def normalize_product_name(name: str) -> str:
    name = name.lower().strip()

    name = re.sub(r'\d+[.,]?\d*\s*%', '', name)

    name = re.sub(r'\d+[.,]?\d*\s*[лЛlL]', '', name)
    name = re.sub(r'\d+[.,]?\d*\s*[гГgG]', '', name)
    name = re.sub(r'\d+[.,]?\d*\s*[кК][гГ]', '', name)

    name = re.sub(r'\d+\s*шт', '', name)
    name = re.sub(r'\d+\s*пак', '', name)

    name = re.sub(r'[,\s]+$', '', name)
    name = name.strip()

    return name


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    db: Session = SessionLocal()
    admin = db.query(Admin).filter(Admin.user_id == user_id).first()
    db.close()
    return admin is not None


async def cmd_start(message: types.Message):
    session = get_session(message.from_user.id)
    session.active = True

    welcome_text = (
        "Добро пожаловать в бот сравнения цен.\n\n"
        "Я помогу вам сравнить цены на продукты в разных магазинах.\n\n"
        "Доступные команды:\n"
        "/add - Добавить товар в корзину\n"
        "/remove - Удалить товар из корзины\n"
        "/cart - Показать корзину\n"
        "/calculate - Рассчитать стоимость корзины\n"
        "/optimize - Оптимальное распределение по магазинам (макс. 2 магазина)\n"
        "/clear - Очистить корзину\n"
        "/bye - Завершить сессию\n"
    )

    if is_admin(message.from_user.id):
        welcome_text += "\nАдминские команды:\n"
        welcome_text += "/upload_excel - Загрузить данные из Excel файла\n"
        welcome_text += "/clear_db - Очистить базу данных продуктов\n"

    await message.answer(welcome_text)


async def cmd_add(message: types.Message, state: FSMContext):
    db: Session = SessionLocal()
    products = db.query(Product).all()
    db.close()

    if not products:
        await message.answer("База данных пуста. Нет доступных продуктов.")
        return

    await message.answer("Введите название товара (например: Молоко, Кефир, Хлеб):")
    await state.set_state(CartStates.waiting_for_product)


async def process_product_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip().lower()

    db: Session = SessionLocal()
    all_products = db.query(Product).all()
    db.close()

    if not all_products:
        await message.answer("База данных пуста. Нет доступных продуктов.")
        await state.clear()
        return

    grouped_products = {}
    for product in all_products:
        norm_name = normalize_product_name(product.name)
        if norm_name not in grouped_products:
            grouped_products[norm_name] = set()
        grouped_products[norm_name].add(product.name)

    normalized_input = normalize_product_name(user_input)

    matched_groups = []
    for norm_name in grouped_products.keys():
        if (normalized_input in norm_name or
            norm_name in normalized_input or
                any(word in norm_name for word in normalized_input.split())):
            matched_groups.append(norm_name)

    if not matched_groups:
        response = f"Товар '{
            user_input}' не найден. Доступные категории товаров:\n"
        for norm_name in sorted(list(grouped_products.keys()))[:10]:
            examples = list(grouped_products[norm_name])[:2]
            examples_str = ", ".join(examples)
            response += f"• {norm_name.capitalize()
                             } (например: {examples_str})\n"
        response += "\nВведите название товара."

        await message.answer(response)
        return

    if len(matched_groups) > 1:
        keyboard_buttons = []
        for norm_name in matched_groups[:5]:
            examples = list(grouped_products[norm_name])[:2]
            examples_str = ", ".join(examples)
            button_text = f"{norm_name.capitalize()} ({examples_str})"
            keyboard_buttons.append(
                [types.KeyboardButton(text=button_text[:40])])
        keyboard_buttons.append([types.KeyboardButton(text="Отмена")])

        keyboard = types.ReplyKeyboardMarkup(
            keyboard=keyboard_buttons,
            resize_keyboard=True
        )

        await state.update_data(matched_groups=matched_groups, grouped_products=grouped_products)
        await message.answer(f"Найдено несколько категорий. Выберите нужную:", reply_markup=keyboard)
        await state.set_state(CartStates.waiting_for_product_selection)
        return

    norm_name = matched_groups[0]
    variants = list(grouped_products[norm_name])

    if len(variants) > 1:
        keyboard_buttons = []
        for variant in sorted(variants)[:10]:
            keyboard_buttons.append([types.KeyboardButton(text=variant)])
        keyboard_buttons.append([types.KeyboardButton(text="Отмена")])

        keyboard = types.ReplyKeyboardMarkup(
            keyboard=keyboard_buttons,
            resize_keyboard=True
        )

        await state.update_data(product_variants=variants, norm_name=norm_name)
        await message.answer(f"Найдено несколько вариантов товара. Выберите нужный:", reply_markup=keyboard)
        await state.set_state(CartStates.waiting_for_product_selection)
        return

    product_name = variants[0]
    await state.update_data(product_name=product_name, norm_name=norm_name)
    await message.answer(f"Введите количество для товара '{product_name}':",
                         reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(CartStates.waiting_for_quantity)


async def process_product_selection(message: types.Message, state: FSMContext):
    """Обработка выбора товара из нескольких вариантов"""
    if message.text == "Отмена":
        await message.answer("Отменено", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    selected = message.text.strip()
    user_data = await state.get_data()

    matched_groups = user_data.get('matched_groups', [])
    grouped_products = user_data.get('grouped_products', {})

    for norm_name in matched_groups:
        if norm_name.capitalize() in selected:
            variants = list(grouped_products[norm_name])
            if len(variants) > 1:
                keyboard_buttons = []
                for variant in sorted(variants)[:10]:
                    keyboard_buttons.append(
                        [types.KeyboardButton(text=variant)])
                keyboard_buttons.append([types.KeyboardButton(text="Отмена")])

                keyboard = types.ReplyKeyboardMarkup(
                    keyboard=keyboard_buttons,
                    resize_keyboard=True
                )

                await state.update_data(product_variants=variants, norm_name=norm_name)
                await message.answer(f"Выберите конкретный товар:", reply_markup=keyboard)
                return
            else:
                product_name = variants[0]
                await state.update_data(product_name=product_name, norm_name=norm_name)
                await message.answer(f"Введите количество для товара '{product_name}':",
                                     reply_markup=types.ReplyKeyboardRemove())
                await state.set_state(CartStates.waiting_for_quantity)
                return

    product_variants = user_data.get('product_variants', [])
    if selected in product_variants:
        norm_name = user_data.get('norm_name')
        await state.update_data(product_name=selected, norm_name=norm_name)
        await message.answer(f"Введите количество для товара '{selected}':",
                             reply_markup=types.ReplyKeyboardRemove())
        await state.set_state(CartStates.waiting_for_quantity)
        return

    await message.answer("Пожалуйста, выберите товар из списка или нажмите 'Отмена'.")


async def process_quantity(message: types.Message, state: FSMContext):
    try:
        quantity = float(
            message.text) if '.' in message.text else int(message.text)
        if quantity <= 0:
            await message.answer("Количество должно быть положительным числом")
            return
    except ValueError:
        await message.answer("Пожалуйста, введите число")
        return

    user_data = await state.get_data()
    product_name = user_data.get('product_name')

    if not product_name:
        await message.answer("Ошибка: не указан товар")
        await state.clear()
        return

    session = get_session(message.from_user.id)

    if product_name in session.cart:
        session.cart[product_name]['quantity'] += quantity
    else:
        session.cart[product_name] = {
            'quantity': quantity
        }

    await message.answer(f"Добавлено {quantity} товара '{product_name}'")
    await state.clear()


async def cmd_cart(message: types.Message):
    session = get_session(message.from_user.id)

    if not session.cart:
        await message.answer("Ваша корзина пуста")
        return

    response = "Ваша корзина:\n\n"
    total_items = 0
    for product_name, data in session.cart.items():
        response += f"{product_name}: {data['quantity']}\n"
        total_items += data['quantity']

    response += f"\nВсего товаров: {total_items}"
    await message.answer(response)


async def cmd_calculate(message: types.Message):
    session = get_session(message.from_user.id)

    if not session.cart:
        await message.answer("Корзина пуста. Добавьте товары с помощью /add")
        return

    db: Session = SessionLocal()
    all_products = db.query(Product).all()
    db.close()

    price_dict = {}
    for product in all_products:
        if product.name not in price_dict:
            price_dict[product.name] = {}
        if product.store not in price_dict[product.name]:
            price_dict[product.name][product.store] = product.price
        else:
            price_dict[product.name][product.store] = min(
                price_dict[product.name][product.store], product.price)

    shop_prices = {}
    missing_products = []

    for product_name, cart_data in session.cart.items():
        if product_name in price_dict:
            for store, price in price_dict[product_name].items():
                if store not in shop_prices:
                    shop_prices[store] = 0
                shop_prices[store] += price * cart_data['quantity']
        else:
            missing_products.append(product_name)

    if missing_products:
        await message.answer(f"Товары не найдены в базе: {', '.join(missing_products)}")
        return

    if not shop_prices:
        await message.answer("Не удалось рассчитать стоимость корзины.")
        return

    response = "Расчет стоимости корзины:\n\n"

    response += "Состав корзины:\n"
    for product_name, cart_data in session.cart.items():
        response += f"• {product_name}: {cart_data['quantity']}\n"
    response += "\n"

    response += "Цены по магазинам:\n"
    for shop, total in sorted(shop_prices.items()):
        response += f"  {shop}: {total:.2f}₽\n"

    response += "\n"

    if shop_prices:
        min_store = min(shop_prices, key=shop_prices.get)
        response += f"Самая низкая цена в одном магазине: {
            min_store} ({shop_prices[min_store]:.2f}₽)"

    await message.answer(response)


async def cmd_optimize(message: types.Message):
    """Оптимальное распределение товаров по магазинам (максимум 2 магазина)"""
    session = get_session(message.from_user.id)

    if not session.cart:
        await message.answer("Корзина пуста. Добавьте товары с помощью /add")
        return

    db: Session = SessionLocal()
    all_products = db.query(Product).all()
    db.close()

    price_dict = {}
    for product in all_products:
        if product.name not in price_dict:
            price_dict[product.name] = {}
        if product.store not in price_dict[product.name]:
            price_dict[product.name][product.store] = product.price
        else:
            price_dict[product.name][product.store] = min(
                price_dict[product.name][product.store], product.price)

    products_in_cart = []
    missing_products = []

    for product_name, cart_data in session.cart.items():
        if product_name in price_dict:
            products_in_cart.append({
                'name': product_name,
                'quantity': cart_data['quantity'],
                'prices': price_dict[product_name]
            })
        else:
            missing_products.append(product_name)

    if missing_products:
        await message.answer(f"Товары не найдены в базе: {', '.join(missing_products)}")
        return

    if not products_in_cart:
        await message.answer("Нет данных для оптимизации.")
        return

    try:
        prob = LpProblem("Minimize_Cost", LpMinimize)

        shops = set()
        for product_data in products_in_cart:
            shops.update(product_data['prices'].keys())
        shops = list(shops)

        if not shops:
            await message.answer("Нет данных о магазинах.")
            return

        x = LpVariable.dicts("x",
                             [(i, j) for i in range(len(products_in_cart))
                              for j in shops],
                             0, 1, LpBinary)

        y = LpVariable.dicts("y", shops, 0, 1, LpBinary)

        prob += lpSum(
            products_in_cart[i]['prices'].get(j, 0) *
            products_in_cart[i]['quantity'] *
            x[(i, j)]
            for i in range(len(products_in_cart)) for j in shops
        )

        for i in range(len(products_in_cart)):
            prob += lpSum(x[(i, j)] for j in shops) == 1

        for j in shops:
            for i in range(len(products_in_cart)):
                prob += x[(i, j)] <= y[j]

        prob += lpSum(y[j] for j in shops) <= 2

        prob.solve()

        if prob.status == 1:
            total_cost = 0
            shop_costs = {shop: 0 for shop in shops}
            shop_products = {shop: [] for shop in shops}

            for i in range(len(products_in_cart)):
                for j in shops:
                    if value(x[(i, j)]) == 1:
                        price = products_in_cart[i]['prices'].get(j, 0)
                        if price > 0:
                            product_cost = price * \
                                products_in_cart[i]['quantity']
                            total_cost += product_cost
                            shop_costs[j] += product_cost
                            shop_products[j].append({
                                'name': products_in_cart[i]['name'],
                                'quantity': products_in_cart[i]['quantity'],
                                'price': price,
                                'total': product_cost
                            })

            used_shops = [shop for shop in shops if shop_costs[shop] > 0]

            if not used_shops:
                await message.answer("Не удалось найти оптимальное распределение.")
                return

            response = "Оптимальное распределение товаров (максимум 2 магазина):\n\n"

            response += "Состав корзины:\n"
            for product_name, cart_data in session.cart.items():
                response += f"• {product_name}: {cart_data['quantity']}\n"
            response += "\n"

            response += f"Общая минимальная стоимость: {total_cost:.2f}₽\n"
            response += f"Используемые магазины: {', '.join(used_shops)}\n\n"

            for shop in used_shops:
                if shop_costs[shop] > 0:
                    response += f"Магазин: {shop}\n"
                    response += f"Стоимость в этом магазине: {
                        shop_costs[shop]:.2f}₽\n"
                    response += "Товары:\n"

                    for item in shop_products[shop]:
                        response += f"  {item['name']}: {item['quantity']
                                                         } × {item['price']}₽ = {item['total']:.2f}₽\n"

                    response += "\n"

            single_shop_prices = {}
            for shop in shops:
                total = 0
                for product_data in products_in_cart:
                    price = product_data['prices'].get(shop, 0)
                    if price > 0:
                        total += price * product_data['quantity']
                if total > 0:
                    single_shop_prices[shop] = total

            if single_shop_prices:
                min_single_shop = min(single_shop_prices,
                                      key=single_shop_prices.get)
                min_single_price = single_shop_prices[min_single_shop]

                response += "Сравнение с покупкой в одном магазине:\n"
                response += f"Минимальная цена в одном магазине ({min_single_shop}): {
                    min_single_price:.2f}₽\n"
                response += f"Экономия от оптимизации: {
                    min_single_price - total_cost:.2f}₽\n"
                response += f"Процент экономии: {
                    ((min_single_price - total_cost) / min_single_price * 100):.1f}%"

        else:
            response = "Не удалось найти оптимальное решение."

    except Exception as e:
        response = f"Ошибка при оптимизации: {str(e)}"

    await message.answer(response)


async def cmd_remove(message: types.Message, state: FSMContext):
    """Команда для удаления товара из корзины"""
    session = get_session(message.from_user.id)

    if not session.cart:
        await message.answer("Корзина уже пуста")
        return

    keyboard_buttons = []
    for product_name in session.cart.keys():
        keyboard_buttons.append([types.KeyboardButton(text=product_name)])
    keyboard_buttons.append([types.KeyboardButton(text="Отмена")])

    keyboard = types.ReplyKeyboardMarkup(
        keyboard=keyboard_buttons,
        resize_keyboard=True
    )

    await message.answer("Выберите товар для удаления из корзины:", reply_markup=keyboard)
    await state.set_state(CartStates.waiting_for_remove_product)


async def process_remove_product(message: types.Message, state: FSMContext):
    """Обработка выбора товара для удаления"""
    if message.text == "Отмена":
        await message.answer("Удаление отменено", reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return

    product_name = message.text.strip()
    session = get_session(message.from_user.id)

    if product_name in session.cart:
        del session.cart[product_name]
        await message.answer(f"Товар '{product_name}' полностью удален из корзины",
                             reply_markup=types.ReplyKeyboardRemove())
    else:
        await message.answer("Товар не найден в корзине",
                             reply_markup=types.ReplyKeyboardRemove())

    await state.clear()


async def cmd_clear(message: types.Message):
    session = get_session(message.from_user.id)
    session.cart.clear()
    await message.answer("Корзина очищена")


async def cmd_bye(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)

    if session.cart:
        items_count = sum(item['quantity'] for item in session.cart.values())
        await message.answer(f"Ваша корзина содержала {items_count} товаров")

    session.cart.clear()
    session.active = False

    await message.answer(
        "Спасибо за использование нашего бота.\n"
        "Ваша корзина очищена. До новых встреч.\n\n"
        "Для начала новой сессии отправьте /start"
    )

    if user_id in sessions:
        del sessions[user_id]


async def cmd_upload_excel(message: types.Message, state: FSMContext):
    """Запуск процесса загрузки Excel файла"""
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "Отправьте Excel файл с данными о ценах.\n\n"
        "Формат файла (3 столбца без заголовков):\n"
        "1. Название продукта\n"
        "2. Название магазина\n"
        "3. Цена в рублях\n\n"
        "Пример содержимого:\n"
        "Хлеб пшеничный нарезка\tПятерочка\t44\n"
        "Хлеб пшеничный нарезка\tДикси\t56\n"
        "Молоко 0,95л\tПятерочка\t95\n\n"
        "Данные будут добавлены в базу без удаления существующих записей."
    )
    await state.set_state(ExcelUploadStates.waiting_for_file)


async def process_excel_file(message: types.Message, state: FSMContext):
    """Обработка загруженного Excel файла"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.document:
        await message.answer("Пожалуйста, отправьте файл Excel.")
        return

    file_name = message.document.file_name
    if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
        await message.answer("Пожалуйста, отправьте файл в формате Excel (.xlsx или .xls).")
        return

    try:
        file_info = await message.bot.get_file(message.document.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
            tmp_file.write(downloaded_file.read())
            tmp_file_path = tmp_file.name

        df = pd.read_excel(tmp_file_path, header=None)
        os.unlink(tmp_file_path)

        if df.shape[1] < 3:
            await message.answer("Ошибка: файл должен содержать минимум 3 столбца.")
            return

        db: Session = SessionLocal()

        added_count = 0
        error_count = 0

        for _, row in df.iterrows():
            try:
                if len(row) >= 3:
                    product_name = str(row[0]).strip()
                    store_name = str(row[1]).strip()

                    try:
                        price = float(row[2])
                        if price <= 0:
                            error_count += 1
                            continue
                    except (ValueError, TypeError):
                        error_count += 1
                        continue

                    existing = db.query(Product).filter(
                        Product.name == product_name,
                        Product.store == store_name,
                        Product.price == price
                    ).first()

                    if not existing:
                        product = Product(
                            name=product_name,
                            store=store_name,
                            price=price
                        )
                        db.add(product)
                        added_count += 1

            except Exception as e:
                error_count += 1
                print(f"Ошибка при обработке строки: {e}")

        db.commit()
        db.close()

        report = (
            f"Данные из Excel файла успешно добавлены.\n\n"
            f"Статистика:\n"
            f"- Добавлено новых записей: {added_count}\n"
            f"- Записей с ошибками: {error_count}\n\n"
            f"Примечание: существующие записи с такими же ценами не дублируются."
        )

        await message.answer(report)

    except Exception as e:
        await message.answer(f"Ошибка при обработке файла: {str(e)}")

    await state.clear()


async def cmd_clear_db(message: types.Message):
    """Очистка базы данных продуктов"""
    if not is_admin(message.from_user.id):
        return

    db: Session = SessionLocal()
    count = db.query(Product).count()
    db.query(Product).delete()
    db.commit()
    db.close()

    await message.answer(f"База данных продуктов очищена. Удалено {count} записей.")


def register_handlers(dp: Dispatcher):
    dp.message.register(cmd_start, Command("start", "help"))
    dp.message.register(cmd_add, Command("add"))
    dp.message.register(cmd_remove, Command("remove"))
    dp.message.register(cmd_cart, Command("cart"))
    dp.message.register(cmd_calculate, Command("calculate"))
    dp.message.register(cmd_optimize, Command("optimize"))
    dp.message.register(cmd_clear, Command("clear"))
    dp.message.register(cmd_bye, Command("bye"))

    dp.message.register(cmd_upload_excel, Command("upload_excel"))
    dp.message.register(cmd_clear_db, Command("clear_db"))

    dp.message.register(process_product_name, CartStates.waiting_for_product)
    dp.message.register(process_product_selection,
                        CartStates.waiting_for_product_selection)
    dp.message.register(process_quantity, CartStates.waiting_for_quantity)
    dp.message.register(process_remove_product,
                        CartStates.waiting_for_remove_product)

    dp.message.register(process_excel_file, ExcelUploadStates.waiting_for_file,
                        F.content_type == ContentType.DOCUMENT)
