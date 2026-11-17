const STORAGE_KEY = "zhiya_mini_state_v1";

const defaults = {
  child: {
    nickname: "芽芽",
    ageBand: "AGE_3_4",
    interests: ["动物", "勇气"],
    preferredMinutes: 15,
  },
  shelfIds: [2, 3, 4],
  records: [],
  customBooks: [],
  settings: {
    showPinyin: true,
    autoPlay: false,
  },
};

function read() {
  try {
    return Object.assign({}, defaults, wx.getStorageSync(STORAGE_KEY) || {});
  } catch (error) {
    return Object.assign({}, defaults);
  }
}

function write(value) {
  wx.setStorageSync(STORAGE_KEY, value);
  return value;
}

function update(patch) {
  return write(Object.assign({}, read(), patch));
}

module.exports = {
  read,
  write,
  update,
};
