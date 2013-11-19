from flask import Flask, render_template
from flask.ext.sqlalchemy import SQLAlchemy
from LandingNet import utils 
from LandingNet.HttpException import InvalidUsage

app = Flask(__name__)
app.config.from_object('LandingNet.config')
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1000000
db = SQLAlchemy(app)

import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
logging.getLogger('werkzeug').setLevel(logging.DEBUG)

@app.route("/")
def index():
    from sqlalchemy import func
    from LandingNet.models import StackTrace
    # TODO : Update this to query MiniDump and join with product and stacktrace
    traces = StackTrace.query.order_by(StackTrace.updated.desc()).limit(10).all()
    return render_template("index.html", traces=traces)

@app.route("/crash/<int:cid>")
def crash(cid):
    from LandingNet.models import StackTrace, MiniDump
    import json
    trace = StackTrace.query.filter_by(id = cid).first_or_404()
    dumps = MiniDump.query.filter_by(stacktrace_id = trace.id).order_by(MiniDump.timestamp.desc()).all()
    if dumps is None:
        raise InvalidUsage("No dumps for trace " + cid)

    trace.data = json.loads(dumps[0].data)

    return render_template("trace.html", trace=trace, dumps=dumps)
    
@app.route("/minidump/<int:did>")
def trace(did):
    from LandingNet.models import MiniDump
    import json
    dump = MiniDump.query.filter_by(id = did).first_or_404()
    dump.data = json.loads(dump.data)
    return render_template("minidump.html", dump=dump)

@app.route("/upload_symbols", methods=["POST"])
def uploadSymbols():
    from flask import request
    from werkzeug import secure_filename
    import os

    if "symbols" not in request.files:
        raise InvalidUsage("Missing symbols file")

    file = request.files["symbols"]

    if file is None or file.filename.rsplit(".", 1)[1] != "sym":
        raise InvalidUsage("Wrong symbols format, .sym extension expected")

    tmp = file.readline()
    tmp = tmp.split(" ")

    file.seek(0)

    path = os.path.join(app.config["DEBUG_SYMBOLS_DIR"], tmp[4].strip(), tmp[3].strip())

    utils.mkdirs(path)

    file.save(os.path.join(path, tmp[4].strip() + ".sym"))

    return render_template("upload_success.html")

@app.route("/submit", methods=["POST"])
def submit():
    from flask import request
    from LandingNet import models
    from werkzeug import secure_filename
    import os
    import uuid

    minidumpArg = ""
    if "minidump" in request.files:
        minidumpArg = "minidump"
    elif "upload_file_minidump" in request.files: # Special case for OSX breakpad crash reporter
        minidumpArg = "upload_file_minidump"
    else:
        raise InvalidUsage("No minidump specified")

    file = request.files[minidumpArg]

    if file is None or file.filename.rsplit(".", 1)[1] != "dmp":
        raise InvalidUsage("Wrong dump format")

    if "build" not in request.form:
        raise InvalidUsage("Build is not specified")

    if "product" not in request.form:
        raise InvalidUsage("Product is not specified")

    if "version" not in request.form:
        raise InvalidUsage("Version is not specified")

    product = models.Product.query.filter_by(version=request.form["version"], name=request.form["product"]).first()
    if product is None:
        raise InvalidUsage("Product %s version %s not found" % (request.form["product"], request.form["version"]))

    filename = str(uuid.uuid4()) + ".dmp"
    file.save(os.path.join(app.config["MINIDUMP_UPDLOAD_DIR"], filename))

    ret = utils.processMinidump(filename)

    st = models.StackTrace.query.filter_by(signature = ret["signature"]).first()
    if st is None:
        st = models.StackTrace()
        st.count = 0
        st.name = ret["name"]
        st.signature = ret["signature"]
        db.session.add(st)
        db.session.commit()

    md = models.MiniDump()
    md.stacktrace_id = st.id
    md.product_id = product.id
    md.signature = ret["signature"]
    md.minidump = filename
    md.build = request.form["build"]
    md.data = ret["data"]
    md.system_info = ret["systemInfo"]
    md.name = ret["name"]

    st.count = st.count + 1

    db.session.add(md)
    db.session.commit()

    return render_template("upload_success.html")

@app.errorhandler(InvalidUsage)
def handleInvalidUsage(error):
    from flask import jsonify
    return "ERROR : " + error.message + "\r\n", 422

@app.template_filter("datetime")
def format_datetime(value):
    from babel.dates import format_datetime
    return format_datetime(value, "YYYY-MM-dd 'at' HH:mm:ss")

@app.template_filter("normalizeFilename")
def normalizeFilename(value):
    filename = "N/A"

    if isinstance(value, basestring) :
        filename = value.rsplit("/", 1)[1]

    return filename

@app.template_filter("normalizeFrame")
def normalizeFrame(frame):
    if frame.get("function"):
        return frame["function"] + ":" + str(frame["line"])
    else:
        return "N/A"
