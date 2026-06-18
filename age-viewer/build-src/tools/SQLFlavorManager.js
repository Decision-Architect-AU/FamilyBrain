"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _typeof = require("@babel/runtime/helpers/typeof");

Object.defineProperty(exports, "__esModule", {
  value: true
});
exports.getQuery = getQuery;

var path = _interopRequireWildcard(require("path"));

var _fs = _interopRequireDefault(require("fs"));

var _Flavors = _interopRequireDefault(require("../config/Flavors"));

function _getRequireWildcardCache(nodeInterop) { if (typeof WeakMap !== "function") return null; var cacheBabelInterop = new WeakMap(); var cacheNodeInterop = new WeakMap(); return (_getRequireWildcardCache = function _getRequireWildcardCache(nodeInterop) { return nodeInterop ? cacheNodeInterop : cacheBabelInterop; })(nodeInterop); }

function _interopRequireWildcard(obj, nodeInterop) { if (!nodeInterop && obj && obj.__esModule) { return obj; } if (obj === null || _typeof(obj) !== "object" && typeof obj !== "function") { return { "default": obj }; } var cache = _getRequireWildcardCache(nodeInterop); if (cache && cache.has(obj)) { return cache.get(obj); } var newObj = {}; var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor; for (var key in obj) { if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) { var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null; if (desc && (desc.get || desc.set)) { Object.defineProperty(newObj, key, desc); } else { newObj[key] = obj[key]; } } } newObj["default"] = obj; if (cache) { cache.set(obj, newObj); } return newObj; }

// Currently works AGENS / AGE ( in-progress )
var sqlBasePath = path.join(__dirname, '../../sql'); // todo: util.format -> ejs

function getQuery() {
  var flavor = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : _Flavors["default"].AGENS;
  var name = arguments.length > 1 ? arguments[1] : undefined;
  var defaultSqlPath = path.join(sqlBasePath, "./".concat(name, "/default.sql"));
  var sqlPath = path.join(sqlBasePath, "./".concat(name, "/").concat(flavor, ".sql"));

  if (_fs["default"].existsSync(defaultSqlPath)) {
    sqlPath = defaultSqlPath;
  }

  if (!_fs["default"].existsSync(sqlPath)) {
    throw new Error("SQL is not exist, name = ".concat(name));
  }

  return _fs["default"].readFileSync(sqlPath, 'utf8');
}
//# sourceMappingURL=SQLFlavorManager.js.map