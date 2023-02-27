import os
import re
import zoneinfo
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from telegram import Update
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes

from pyhafas import HafasClient
from pyhafas.profile import DBProfile
from sqlalchemy.sql import ClauseElement

engine = create_engine(os.getenv("DATABASE_URI", "sqlite:///db.db"))
Session = sessionmaker(bind=engine)
Session.configure(bind=engine)
Base = declarative_base()

client = HafasClient(DBProfile(), debug=False)


class Stopover(Base):
    __tablename__ = 'stopover'
    station_id = Column(Integer, ForeignKey('station.id'), primary_key=True)
    segment_id = Column(Integer, ForeignKey('segment.id'), primary_key=True)


class Station(Base):
    __tablename__ = "station"
    id = Column(Integer, primary_key=True)
    eva = Column(Integer, unique=True)
    name = Column(String(60))
    latitude = Column(Float)
    longitude = Column(Float)

    segments = relationship("Segment", secondary=Stopover.__tablename__, back_populates="stopovers")


class JourneySegment(Base):
    __tablename__ = 'journeysegment'
    journey_id = Column(Integer, ForeignKey('journey.id'), primary_key=True)
    segment_id = Column(Integer, ForeignKey('segment.id'), primary_key=True)


class Segment(Base):
    __tablename__ = "segment"
    id = Column(Integer, primary_key=True)
    segment_id = Column(String(60), unique=True)
    trainName = Column(String(60))
    trainNumber = Column(String(60))
    trainTo = Column(String(60))
    trainType = Column(String(60))
    arrivalDelay = Column(Integer)
    arrivalScheduledTime = Column(DateTime, default=None)
    arrivalTime = Column(DateTime, default=None)
    departureDelay = Column(Integer)
    departureScheduledTime = Column(DateTime, default=None)
    departureTime = Column(DateTime, default=None)

    origin_id = Column(Integer, ForeignKey('station.id'))
    origin = relationship("Station", foreign_keys=[origin_id])
    destination_id = Column(Integer, ForeignKey('station.id'))
    destination = relationship("Station", foreign_keys=[destination_id])

    stopovers = relationship("Station", secondary=Stopover.__tablename__, back_populates="segments")
    journeys = relationship("Journey", secondary=JourneySegment.__tablename__, back_populates="segments")


class Category(Base):
    __tablename__ = 'category'
    id = Column(Integer, primary_key=True)
    category = Column(String(50))
    color = Column(String(7), default=None)

    userjourneys = relationship("UserJourney", back_populates="category")


class Purpose(Base):
    __tablename__ = 'purpose'
    id = Column(Integer, primary_key=True)
    purpose = Column(String(50))
    color = Column(String(7), default=None)

    userjourneys = relationship("UserJourney", back_populates="purpose")


class UserJourney(Base):
    __tablename__ = 'userjourney'
    user_id = Column(Integer, ForeignKey('user.id'), primary_key=True)
    user = relationship("User", foreign_keys=[user_id])
    journey_id = Column(Integer, ForeignKey('journey.id'), primary_key=True)
    journey = relationship("Journey", foreign_keys=[journey_id])

    message_id = Column(Integer, nullable=False)
    text = Column(String(), default=None)
    price = Column(Integer, default=None)

    category_id = Column(Integer, ForeignKey('category.id'))
    category = relationship("Category", back_populates="userjourneys")
    purpose_id = Column(Integer, ForeignKey('purpose.id'))
    purpose = relationship("Purpose", back_populates="userjourneys")


class Journey(Base):
    __tablename__ = 'journey'
    id = Column(Integer, primary_key=True)
    journey_id = Column(String(), nullable=True, unique=True)

    segments = relationship("Segment", secondary=JourneySegment.__tablename__, back_populates="journeys")
    users = relationship("UserJourney", back_populates="journey")


class User(Base):
    __tablename__ = 'user'
    id = Column(Integer, primary_key=True)
    user_id = Column(String(), nullable=False, unique=True)
    username = Column(String(50), nullable=False, unique=True)

    journeys = relationship("UserJourney", back_populates="user")


def get_or_create(session, model, defaults=None, **kwargs):
    instance = session.query(model).filter_by(**kwargs).one_or_none()
    if instance:
        return instance
    else:
        params = {k: v for k, v in kwargs.items() if not isinstance(v, ClauseElement)}
        params.update(defaults or {})
        instance = model(**params)
        try:
            session.add(instance)
            session.commit()
        except Exception:
            session.rollback()
            instance = session.query(model).filter_by(**kwargs).one()
            return instance
        else:
            return instance


def get_station_by_name(session, name):
    locations = client.locations(name)
    best_location = locations[0]
    return get_or_create(session, Station,
                         {"eva": best_location.id,
                          "name": best_location.name,
                          "longitude": best_location.longitude,
                          "latitude": best_location.latitude},
                         name=name)


def get_journey_or_create_by_journey_id(session, journey_id, segments):
    return get_or_create(session, Journey, {"segments": segments}, journey_id=journey_id)


def get_user_or_create_by_user_id(session, user_id):
    return get_or_create(session, User, {"user_id": user_id, "username": user_id}, user_id=user_id)


def get_segment_or_create_by_origin_destination_departuretime_arrivaltime(session, origin, destination, departureScheduledTime, arrivalScheduledTime):
    journeys = (client.journeys(
        origin=origin.eva,
        destination=destination.eva,
        date=departureScheduledTime,
        min_change_time=0,
        max_changes=0
    ))
    journeys = [j for j in journeys if
                len(j.legs) == 1 and j.legs[0].departure == departureScheduledTime and j.legs[
                    0].arrival == arrivalScheduledTime]
    if not 0 < len(journeys) < 2:
        raise Exception("Could not find a suitable connection!")

    j = journeys[0]
    leg = j.legs[0]

    return get_or_create(session, Segment,
                         {"segment_id": leg.id,
                          "trainName": leg.name,
                          "origin": get_or_create(session, Station, {"eva": leg.origin.id, "name": leg.origin.name,
                                                      "latitude": leg.origin.latitude,
                                                      "longitude": leg.origin.longitude}, eva=leg.origin.id),
                          "destination": get_or_create(session, Station,
                                        {"eva": leg.destination.id, "name": leg.destination.name,
                                         "latitude": leg.destination.latitude,
                                         "longitude": leg.destination.longitude}, eva=leg.destination.id),
                          "stopovers": [
                             get_or_create(session, Station, {"eva": stopover.stop.id, "name": stopover.stop.name,
                                                              "latitude": stopover.stop.latitude,
                                                              "longitude": stopover.stop.longitude},
                                           eva=stopover.stop.id) for stopover in leg.stopovers]},
                         segment_id=leg.id)


def get_userjourney_by_user_journey(session, user, journey, message_id, text):
    return get_or_create(session, UserJourney, {"user": user,
                                                "journey": journey,
                                                "message_id": message_id,
                                                "text": text},
                         user=user,
                         journey=journey)


def get_category_or_create_by_category(session, category, color=None):
    return get_or_create(session, Category, {"category": category,
                                             "color": color},
                         category=category)


def get_purpose_or_create_by_purpose(session, purpose, color=None):
    return get_or_create(session, Purpose, {"purpose": purpose,
                                             "color": color},
                         purpose=purpose)


def split_on_empty_lines(s):
    # greedily match 2 or more new-lines
    blank_line_regex = r"(?:\r?\n){2,}"
    return re.split(blank_line_regex, s.strip())


def split_on_new_lines(s):
    # greedily match new-line
    blank_line_regex = r"(?:\r?\n){1,}"
    return re.split(blank_line_regex, s.strip())


async def toDatabase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Triggered toDatabase command by %i" % update.effective_user.id)
    input = update.message.text
    try:
        with Session() as session:
            k = split_on_empty_lines(input)

            if len(k) <= 1:
                raise Exception("Journey is missing or too short!")

            l = split_on_new_lines(k[0])

            if not re.match("([0-9]+)\.([0-9]+)\.([0-9]+)", l[1]):
                raise Exception("Date is in wrong format or missing!")

            date = datetime.strptime(l[1], '%d.%m.%Y')

            segments = []
            for s in k[1:]:
                l = split_on_new_lines(s)
                if len(l) < 4:
                    raise Exception("Segment lines are in wrong format or missing!")

                d = l[2].split(",")[0].split(" ")
                departureScheduledTime = datetime.combine(date, datetime.strptime(d[1], "%H:%M").time()).replace(tzinfo=zoneinfo.ZoneInfo(key="Europe/Berlin"))
                origin = get_station_by_name(session, " ".join(d[2:]))
                d = l[3].split(",")[0].split(" ")
                arrivalScheduledTime = datetime.combine(date, datetime.strptime(d[1], "%H:%M").time()).replace(tzinfo=zoneinfo.ZoneInfo(key="Europe/Berlin"))
                destination = get_station_by_name(session, " ".join(d[2:]))
                print(origin.name, "to", destination.name)

                segments.append(get_segment_or_create_by_origin_destination_departuretime_arrivaltime(session, origin, destination, departureScheduledTime, arrivalScheduledTime))

            journey_id = "#".join([s.segment_id for s in segments])
            user = get_user_or_create_by_user_id(session, update.effective_user.id)
            journey = get_journey_or_create_by_journey_id(session, journey_id, segments)
            userjourney = get_userjourney_by_user_journey(session, user, journey, update.message.id, update.message.text)

            session.add(userjourney)
            session.commit()
            if userjourney.message_id == update.message.id:
                print("Saved Journey %i to database" % journey.id)
                await update.message.reply_text("\u2705 Saved in database!", reply_to_message_id=update.message.id)
            else:
                print("Duped Journey %i" % journey.id)
                await update.message.reply_text("\uE737 Duped Journey. Original Journey", reply_to_message_id=userjourney.message_id)
                await update.message.reply_text("\uE737 Ignored.", reply_to_message_id=update.message.id)
    except Exception as e:
        print("Could not fetch message. e.args: %s" % e.args)
        await update.message.reply_text("\uE550 Could not fetch this message! e.args: %s" % e.args, reply_to_message_id=update.message.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Triggered start command by %i" % update.effective_user.id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="I'm a bot, please talk to me!"
    )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with Session() as session:
            instance = session.query(UserJourney).filter_by(
                user=get_user_or_create_by_user_id(session, user_id=update.effective_user.id),
                message_id=update.message.reply_to_message.id).one_or_none()

            if instance is None:
                raise Exception("Couldnt find journey! Maybe its deleted or a dupe.")

            instance.delete()
            session.commit()
            print("Deleted journey")
            await update.message.reply_text("\u2705 Deleted journey", reply_to_message_id=update.message.id)
    except Exception as e:
        print("Deletion failed! e.args: %s" % e.args)
        await update.message.reply_text("\uE550 Setting price failed! e.args: %s" % e.args,
                                        reply_to_message_id=update.message.id)


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 0 or len(context.args) > 1:
            raise Exception("Received too less or too much arguments!")

        price = context.args[0]
        with Session() as session:
            instance = session.query(UserJourney).filter_by(user=get_user_or_create_by_user_id(session, user_id=update.effective_user.id), message_id=update.message.reply_to_message.id).one_or_none()

            if instance is None:
                raise Exception("Couldnt find journey! Maybe its deleted or a dupe.")

            if price == "None":
                instance.price = None
            else:
                price = price.replace(",", ".")
                if not price.replace(".", "").isdigit():
                   raise Exception("Received price in wrong format!")
                instance.price = float(price) * 100
            session.add(instance)
            session.commit()
            print("Price set to %s" % price)
            await update.message.reply_text("\u2705 Price set to %s" % price, reply_to_message_id=update.message.id)
    except Exception as e:
        print("Setting price failed! e.args: %s" % e.args)
        await update.message.reply_text("\uE550 Setting price failed! e.args: %s" % e.args, reply_to_message_id=update.message.id)


async def category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 0 or len(context.args) > 2:
            raise Exception("Received too less or too much arguments!")

        category = context.args[0]
        with Session() as session:
            instance = session.query(UserJourney).filter_by(user=get_user_or_create_by_user_id(session, user_id=update.effective_user.id), message_id=update.message.reply_to_message.id).one_or_none()

            if instance is None:
                raise Exception("Couldnt find journey! Maybe its deleted or a dupe.")

            category = category.lower()
            if category == "none":
                instance.category = None
            else:
                if len(context.args) == 2:
                    color = context.args[1]
                    if not re.fullmatch(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color):
                        raise Exception("Received color in wrong format!")
                    instance.category = get_category_or_create_by_category(session, category, color)
                else:
                    instance.category = get_category_or_create_by_category(session, category)
            session.add(instance)
            session.commit()
            print("Category set to %s" % category)
            await update.message.reply_text("\u2705 Category set to %s" % category, reply_to_message_id=update.message.id)
    except Exception as e:
        print("Setting category failed! e.args: %s" % e.args)
        await update.message.reply_text("\uE550 Setting category failed! e.args: %s" % e.args, reply_to_message_id=update.message.id)


async def purpose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 0 or len(context.args) > 2:
            raise Exception("Received too less or too much arguments!")

        purpose = context.args[0]
        with Session() as session:
            instance = session.query(UserJourney).filter_by(user=get_user_or_create_by_user_id(session, user_id=update.effective_user.id), message_id=update.message.reply_to_message.id).one_or_none()

            if instance is None:
                raise Exception("\uE550 Could not find journey! Maybe its deleted or a dupe.")

            purpose = purpose.lower()
            if purpose == "none":
                instance.purpose = None
            else:
                if len(context.args) == 2:
                    color = context.args[1]
                    if not re.fullmatch(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color):
                        raise Exception("Received color in wrong format!")
                    instance.purpose = get_purpose_or_create_by_purpose(session, purpose, color)
                else:
                    instance.purpose = get_purpose_or_create_by_purpose(session, purpose)
            session.add(instance)
            session.commit()
            print("Purpose set to %s" % purpose)
            await update.message.reply_text("\u2705 Purpose set to %s" % purpose, reply_to_message_id=update.message.id)
    except Exception as e:
        print("Setting purpose failed! e.args: %s" % e.args)
        await update.message.reply_text("\uE550 Setting purpose failed! e.args: %s" % e.args, reply_to_message_id=update.message.id)

async def username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 0 or len(context.args) > 1:
            raise Exception("Received too less or too much arguments!")

        username = context.args[0]
        with Session() as session:
            instance = session.query(User).filter_by(username=username).one_or_none()

            if instance is not None:
                raise Exception("Username already in use!")

            instance = get_user_or_create_by_user_id(session, user_id=update.effective_user.id)

            if username == "None":
                raise Exception("Username is invalid!")
            else:
                instance.username = username
            session.add(instance)
            session.commit()
            print("Username set to %s" % username)
            await update.message.reply_text("\u2705 Username set to %s" % username, reply_to_message_id=update.message.id)
    except Exception as e:
        print("Setting username failed! e.args: %s" % e.args)
        await update.message.reply_text("\uE550 Setting username failed! e.args: %s" % e.args, reply_to_message_id=update.message.id)

if __name__ == "__main__":
    Base.metadata.create_all(engine)

    print("Starting Telegram Bot...")
    applicationBuilder = ApplicationBuilder().token(os.getenv("TELEGRAM_TOKEN"))

    if os.getenv("HTTP_PROXY", None):
        applicationBuilder.proxy_url(os.getenv("HTTP_PROXY"))
        applicationBuilder.get_updates_proxy_url(os.getenv("HTTP_PROXY"))

    application = applicationBuilder.build()

    #await application.bot.set_my_commands([
    #    BotCommand("start", "Start the bot and get the welcome message"),
    #    BotCommand("delete", "Deletes a journey, by replying to it with this command"),
    #    BotCommand("price", "Sets the prices of a journey, by replying to it with this command"),
    #    BotCommand("category", "Sets the category of a journey, by replying to it with this command"),
    #    BotCommand("purpose", "Sets the purpose of a journey, by replying to it with this command"),
    #    BotCommand("username", "Sets your username"),
    #])

    print("Creating handler")
    start_handler = CommandHandler('start', start)
    delete_handler = CommandHandler('delete', delete)
    price_handler = CommandHandler('price', price)
    category_handler = CommandHandler('category', category)
    purpose_handler = CommandHandler('purpose', purpose)
    username_handler = CommandHandler('username', username)
    toDatabase_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), toDatabase)

    print("Adding handler")
    application.add_handler(start_handler)
    application.add_handler(delete_handler)
    application.add_handler(price_handler)
    application.add_handler(category_handler)
    application.add_handler(purpose_handler)
    application.add_handler(username_handler)
    application.add_handler(toDatabase_handler)

    application.run_polling()

